"""Long-lived SQLFluff worker processes that replace one interpreter launch per command.

``python -m sqlfluff parse|format`` costs an interpreter start plus the SQLFluff import on
every call, and formatting one object takes three such calls. A worker runs the same SQLFluff
command-line entry point, with the same arguments, inside one interpreter that stays alive,
so the cost is paid once per worker instead of three times per object.

The worker is a drop-in ``Runner``: callers still pass ``[python, "-m", "sqlfluff", ...]`` and
receive a ``CompletedProcess``. Anything else, or any worker failure, falls back to one
ordinary subprocess, so results never depend on whether the worker was available.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import secrets
import subprocess
import threading
from collections.abc import Callable, Sequence

from sqlctx.formatting.manager import Runner, _default_runner

COMMAND_TIMEOUT_SECONDS = 120
# Each worker holds one interpreter with SQLFluff loaded (roughly 100 MB); two to four of them
# keep formatting off the critical path without competing with the database reads.
WORKERS_PER_INTERPRETER = min(4, max(2, (os.cpu_count() or 2) // 2))
# A worker is recycled after this many commands so SQLFluff caches cannot grow without bound.
COMMANDS_PER_WORKER = 2000

# Runs inside the selected host interpreter. It is passed with ``-c``, so the current working
# directory is dropped from ``sys.path`` before SQLFluff is imported, matching what a module
# launch would resolve from the installed environment only.
_WORKER_SOURCE = r"""
import sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
import contextlib, io, json, logging
marker = sys.argv[1]
reply = sys.stdout
from sqlfluff.cli.commands import cli
import click
reply.write(marker + json.dumps({"ready": True}) + "\n")
reply.flush()
for line in sys.stdin:
    request = json.loads(line)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    logging.getLogger("sqlfluff").handlers.clear()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(args=request["args"], prog_name="sqlfluff", standalone_mode=False)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except click.exceptions.Exit as exc:
            code = exc.exit_code
        except click.ClickException as exc:
            exc.show(file=err)
            code = exc.exit_code
        except click.exceptions.Abort:
            code = 1
        except Exception as exc:
            err.write(type(exc).__name__ + "\n")
            code = 1
    reply.write(marker + json.dumps(
        {"code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}
    ) + "\n")
    reply.flush()
"""

Spawner = Callable[[Sequence[str]], subprocess.Popen[str]]


def _default_spawner(command: Sequence[str]) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # The selected absolute Python and closed internal arguments assemble every command.
    return subprocess.Popen(  # noqa: S603
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=environment,
    )


class WorkerUnavailable(Exception):
    """The worker could not answer; the caller falls back to one subprocess."""


class _Worker:
    def __init__(self, python: str, spawner: Spawner) -> None:
        self.marker = "@@sqlctx-" + secrets.token_hex(8) + "@@"
        self.process = spawner([python, "-c", _WORKER_SOURCE, self.marker])
        self.replies: queue.Queue[str | None] = queue.Queue()
        self.commands = 0
        reader = threading.Thread(target=self._read, name="sqlctx-sqlfluff-reader", daemon=True)
        reader.start()
        ready = self._reply(COMMAND_TIMEOUT_SECONDS)
        if not ready.get("ready"):
            self.close()
            raise WorkerUnavailable("worker did not start")

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self.replies.put(None)
            return
        for line in stream:
            # Only framed lines are replies; anything else a library prints is ignored.
            if line.startswith(self.marker):
                self.replies.put(line[len(self.marker) :])
        self.replies.put(None)

    def _reply(self, timeout: float) -> dict[str, object]:
        try:
            raw = self.replies.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise subprocess.TimeoutExpired("sqlfluff", timeout) from exc
        if raw is None:
            self.close()
            raise WorkerUnavailable("worker exited")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.close()
            raise WorkerUnavailable("unreadable reply") from exc
        if not isinstance(value, dict):
            self.close()
            raise WorkerUnavailable("unexpected reply")
        return value

    def run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        stdin = self.process.stdin
        if stdin is None or self.process.poll() is not None:
            raise WorkerUnavailable("worker is not running")
        try:
            stdin.write(json.dumps({"args": list(command[3:])}) + "\n")
            stdin.flush()
        except OSError as exc:
            self.close()
            raise WorkerUnavailable("worker input closed") from exc
        reply = self._reply(COMMAND_TIMEOUT_SECONDS)
        self.commands += 1
        code = reply.get("code")
        return subprocess.CompletedProcess(
            list(command),
            code if isinstance(code, int) else 1,
            str(reply.get("stdout", "")),
            str(reply.get("stderr", "")),
        )

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def close(self) -> None:
        if self.process.poll() is None:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


class PersistentSqlFluffRunner:
    """A ``Runner`` that serves ``python -m sqlfluff`` commands from reusable workers."""

    def __init__(
        self,
        *,
        fallback: Runner = _default_runner,
        spawner: Spawner = _default_spawner,
        workers_per_interpreter: int = WORKERS_PER_INTERPRETER,
    ) -> None:
        self.fallback = fallback
        self.spawner = spawner
        self.workers_per_interpreter = max(1, workers_per_interpreter)
        self._idle: dict[str, list[_Worker]] = {}
        self._started: dict[str, int] = {}
        self._broken: set[str] = set()
        self._condition = threading.Condition()
        self._closed = False

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if len(command) < 3 or list(command[1:3]) != ["-m", "sqlfluff"]:
            return self.fallback(command)
        python = str(command[0])
        worker = self._acquire(python)
        if worker is None:
            return self.fallback(command)
        try:
            result = worker.run(command)
        except WorkerUnavailable:
            self._release(python, worker, healthy=False)
            return self.fallback(command)
        except subprocess.TimeoutExpired:
            self._release(python, worker, healthy=False)
            raise
        self._release(python, worker, healthy=True)
        return result

    def _acquire(self, python: str) -> _Worker | None:
        with self._condition:
            while True:
                if self._closed or python in self._broken:
                    return None
                idle = self._idle.setdefault(python, [])
                if idle:
                    return idle.pop()
                if self._started.get(python, 0) < self.workers_per_interpreter:
                    self._started[python] = self._started.get(python, 0) + 1
                    break
                self._condition.wait()
        try:
            return _Worker(python, self.spawner)
        except (WorkerUnavailable, OSError, subprocess.TimeoutExpired):
            with self._condition:
                self._started[python] -= 1
                # An interpreter that cannot host a worker uses plain subprocesses from now on.
                self._broken.add(python)
                self._condition.notify_all()
            return None

    def _release(self, python: str, worker: _Worker, *, healthy: bool) -> None:
        keep = healthy and worker.alive and worker.commands < COMMANDS_PER_WORKER
        if not keep:
            worker.close()
        with self._condition:
            if keep and not self._closed:
                self._idle.setdefault(python, []).append(worker)
            else:
                self._started[python] -= 1
                if keep:
                    worker.close()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            workers = [worker for idle in self._idle.values() for worker in idle]
            self._idle.clear()
            self._condition.notify_all()
        for worker in workers:
            worker.close()


_shared: PersistentSqlFluffRunner | None = None
_shared_lock = threading.Lock()


def shared_runner() -> PersistentSqlFluffRunner:
    """The process-wide runner; its workers are stopped when the service exits."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = PersistentSqlFluffRunner()
            atexit.register(_shared.close)
        return _shared
