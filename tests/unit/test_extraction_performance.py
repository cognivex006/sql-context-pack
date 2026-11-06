"""Connection reuse, parallel extraction and persistent formatting keep results unchanged."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from threading import Event
from typing import Any

import pytest

import sqlctx.application.catalog as catalog_module
from sqlctx.adapters.mysql import MySqlAdapter
from sqlctx.application.catalog import CatalogRequest, _ordered_parallel
from sqlctx.core.enums import MaterializationMode
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import MaterializationSelection
from sqlctx.exporting.writer import _ordered_map
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.formatting.manager import SqlFluffManager, _default_runner
from sqlctx.formatting.worker import PersistentSqlFluffRunner
from sqlctx.security.runtime import JsonRuntimeStateStore
from tests.integration.test_catalog_service import (
    CatalogCursor,
    make_service,
    resolved_profile,
)

TABLE_COUNT = 30


class ManyTablesCursor(CatalogCursor):
    def execute(self, query: str, parameters: Any = ()) -> None:
        super().execute(query, parameters)
        if " as object_type" in " ".join(query.lower().split()):
            self.rows = [(f"UM_T{index:02d}", "table") for index in range(TABLE_COUNT)]


class CountingConnection:
    opened = 0
    lock = threading.Lock()

    def __init__(self) -> None:
        with CountingConnection.lock:
            CountingConnection.opened += 1
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> ManyTablesCursor:
        return ManyTablesCursor()

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _all_selection() -> MaterializationSelection:
    return MaterializationSelection(mode=MaterializationMode.ALL)


def test_extraction_reuses_one_connection_per_worker(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CountingConnection())
    request = CatalogRequest(profile="demo", schemas=["app"], object_types=["table"])
    accepted = service.create(request, resolved_profile(), adapter)
    CountingConnection.opened = 0

    status = service.select(accepted.catalog_id, _all_selection())

    assert status.fully_analyzed_object_count == TABLE_COUNT
    # Before reuse every statement opened its own connection: several per table.
    assert CountingConnection.opened <= catalog_module.EXTRACTION_WORKERS


def test_parallel_extraction_keeps_catalog_order_and_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(workers: int, root: Path) -> list[tuple[str, list[str]]]:
        monkeypatch.setattr(catalog_module, "EXTRACTION_WORKERS", workers)
        service, _ = make_service(root)
        adapter = MySqlAdapter(lambda _: CountingConnection())
        request = CatalogRequest(profile="demo", schemas=["app"], object_types=["table"])
        accepted = service.create(request, resolved_profile(), adapter)
        service.select(accepted.catalog_id, _all_selection())
        snapshot = service.get_snapshot(accepted.catalog_id)
        return [
            (item.ref.object_id, [column.name for column in item.columns])
            for item in snapshot.objects
        ]

    sequential = run(1, tmp_path / "one")
    parallel = run(4, tmp_path / "four")
    assert parallel == sequential
    assert [object_id for object_id, _ in parallel] == [
        f"table:app.UM_T{index:02d}" for index in range(TABLE_COUNT)
    ]


class FlakyCursor(CatalogCursor):
    def __init__(self, connection: FlakyConnection) -> None:
        super().__init__()
        self.connection = connection

    def execute(self, query: str, parameters: Any = ()) -> None:
        if self.connection.broken and not query.lower().startswith("set "):
            raise RuntimeError("server closed the connection")
        super().execute(query, parameters)


class FlakyConnection:
    created: list[FlakyConnection] = []

    def __init__(self) -> None:
        self.broken = False
        self.closed = False
        FlakyConnection.created.append(self)

    def cursor(self) -> FlakyCursor:
        return FlakyCursor(self)

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_a_dropped_pooled_connection_is_replaced_once_and_closed() -> None:
    FlakyConnection.created = []
    adapter = MySqlAdapter(lambda _: FlakyConnection())
    profile = resolved_profile()
    with adapter.connection_scope(profile):
        adapter.get_server_info(profile)
        first = FlakyConnection.created[0]
        first.broken = True
        adapter.get_server_info(profile)
        assert first.closed
        assert len(FlakyConnection.created) == 2
        assert not FlakyConnection.created[1].closed
    assert FlakyConnection.created[1].closed


def test_a_fresh_connection_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FlakyConnection):
        def __init__(self) -> None:
            super().__init__()
            self.broken = True

    FlakyConnection.created = []
    adapter = MySqlAdapter(lambda _: Broken())
    monkeypatch.setattr(adapter, "_record_failure", lambda *_: {})
    with pytest.raises(SqlCtxError) as error:
        adapter.get_server_info(resolved_profile())
    assert error.value.code == "DATABASE_OPERATION_FAILED"
    assert len(FlakyConnection.created) == 1


def test_statements_outside_a_scope_still_open_and_close_their_own_connection() -> None:
    CountingConnection.opened = 0
    adapter = MySqlAdapter(lambda _: CountingConnection())
    profile = resolved_profile()
    adapter.get_server_info(profile)
    adapter.get_server_info(profile)
    assert CountingConnection.opened == 2


def test_cancel_reaches_every_running_cursor() -> None:
    adapter = MySqlAdapter(lambda _: CountingConnection())

    class Cancellable:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    first, second = Cancellable(), Cancellable()
    adapter._track_cursor(first)  # type: ignore[arg-type]
    adapter._track_cursor(second)  # type: ignore[arg-type]
    assert adapter.cancel()
    assert first.cancelled and second.cancelled


def test_ordered_parallel_yields_in_input_order_and_bounds_work() -> None:
    running = 0
    peak = 0
    lock = threading.Lock()

    def work(item: int) -> int:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.01 * (5 - item % 5))
        with lock:
            running -= 1
        return item * 10

    results = list(_ordered_parallel(range(20), work, workers=3, initializer=None, stop=Event()))
    assert results == [(item, item * 10) for item in range(20)]
    assert peak <= 3


def test_ordered_parallel_stops_submitting_after_stop() -> None:
    stop = Event()
    started: list[int] = []

    def work(item: int) -> int:
        started.append(item)
        return item

    consumed = []
    for item, _ in _ordered_parallel(range(100), work, workers=2, initializer=None, stop=stop):
        consumed.append(item)
        if item == 3:
            stop.set()
    assert consumed[:4] == [0, 1, 2, 3]
    assert len(started) < 100


def test_ordered_map_preserves_order() -> None:
    items = list(range(12))
    assert list(_ordered_map(items, lambda item: -item, workers=4)) == [
        (item, -item) for item in items
    ]


def test_non_sqlfluff_commands_use_the_fallback_runner() -> None:
    calls: list[list[str]] = []

    def fallback(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(list(command), 0, "ok", "")

    def spawner(command: Sequence[str]) -> subprocess.Popen[str]:
        raise AssertionError("no worker for a non-SQLFluff command")

    runner = PersistentSqlFluffRunner(fallback=fallback, spawner=spawner)
    assert runner(["python", "-c", "print(1)"]).stdout == "ok"
    assert calls == [["python", "-c", "print(1)"]]


def test_an_interpreter_that_cannot_host_a_worker_falls_back_to_subprocesses() -> None:
    spawned = 0

    def fallback(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), 0, "fallback", "")

    def spawner(command: Sequence[str]) -> subprocess.Popen[str]:
        nonlocal spawned
        spawned += 1
        raise OSError("cannot start")

    runner = PersistentSqlFluffRunner(fallback=fallback, spawner=spawner)
    for _ in range(3):
        assert runner(["python", "-m", "sqlfluff", "version"]).stdout == "fallback"
    assert spawned == 1


@pytest.mark.skipif(importlib.util.find_spec("sqlfluff") is None, reason="SQLFluff not installed")
def test_worker_formatting_is_identical_to_one_subprocess_per_command(tmp_path: Path) -> None:
    samples = [
        "create or alter procedure dbo.P1 as begin select a,b from dbo.T1 where x = 1 end\n",
        "select from where (((\n",
    ]
    outputs = {}
    for label, runner in (
        ("subprocess", _default_runner),
        ("worker", PersistentSqlFluffRunner(workers_per_interpreter=1)),
    ):
        manager = SqlFluffManager(
            JsonRuntimeStateStore(tmp_path / label), python_executable=Path(sys.executable)
        )
        formatter = SqlFluffFormatter(manager, runner=runner)
        outputs[label] = [
            formatter.format_one(object_id=f"o{index}", sql=sql, dialect="tsql")
            for index, sql in enumerate(samples)
        ]
        if isinstance(runner, PersistentSqlFluffRunner):
            runner.close()
    for expected, actual in zip(outputs["subprocess"], outputs["worker"], strict=True):
        assert actual.status == expected.status
        assert actual.content == expected.content
    assert [str(item.status) for item in outputs["worker"]] == ["formatted", "parse_failed"]
