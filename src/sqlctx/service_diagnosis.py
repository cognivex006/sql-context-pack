"""Name the shared-service fault from evidence and apply the owner-requested fix.

Diagnosis is read-only: one bounded health probe plus process/socket listings. Evidence holds
only port numbers, process ids, start times and socket counts — never URLs, tokens, metadata,
database hosts or SQL. Applying a fix happens only through the owner-typed `doctor fix`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from sqlctx.core.errors import SqlCtxError

SERVICE_NAME = "SQLContextPack"
DEFAULT_PORT = 8765
HEALTH_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 10
_STOP_WAIT_SECONDS = 20.0

# fix_id -> what applying it does, in the owner's terms.
_FIX_EFFECTS = {
    "service-start": "Starts the shared service through its supervisor. Nothing is deleted.",
    "service-restart": (
        "Stops the shared service, force-stops its listener process only if it is still the "
        "SQL Context Pack service, then starts it. In-flight jobs are cancelled; profiles, "
        "credentials, registered folders and retained jobs are kept."
    ),
    "service-repair": (
        "Runs sqlctx repair --component service: restages service files, issues a fresh agent "
        "token and restarts the service. Profiles and credentials are kept."
    ),
    "none": "Doctor does not stop a process it does not own; stop it yourself.",
}

_DIAGNOSES = {
    "SERVICE_DOWN": "Nothing is accepting connections on the service port; the service is not running.",
    "SERVICE_HUNG": (
        "The service process accepts connections but does not answer /api/v1/health in time; "
        "it is running but stuck, often behind a database call that never returned."
    ),
    "SERVICE_AUTH_REJECTED": "The service rejects the agent token in the connection metadata.",
    "SERVICE_METADATA_INVALID": "Connection metadata exists but cannot be used to reach the service.",
    "SERVICE_UNHEALTHY": "The service answers but reports a health status other than ok.",
    "PORT_OWNER_MISMATCH": (
        "The service port is held by a process that is not the registered SQL Context Pack "
        "service, so the bridge reaches the wrong server."
    ),
    "SERVICE_UNAVAILABLE": "The health probe failed for a reason doctor could not classify.",
}

_FIX_FOR_CODE = {
    "SERVICE_DOWN": "service-start",
    "SERVICE_HUNG": "service-restart",
    "SERVICE_AUTH_REJECTED": "service-repair",
    "SERVICE_METADATA_INVALID": "service-repair",
    "SERVICE_UNHEALTHY": "service-repair",
    "SERVICE_UNAVAILABLE": "service-restart",
    "PORT_OWNER_MISMATCH": "none",
}

SERVICE_CODES = frozenset(_FIX_FOR_CODE)

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str] | None"]


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run one fixed, read-only system listing; None when the tool is absent or times out."""
    executable = shutil.which(arguments[0])
    if executable is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - resolved system tool with fixed arguments.
            [executable, *arguments[1:]],
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def probe_health(
    base: str, token: str, *, transport: httpx.BaseTransport | None = None
) -> tuple[str | None, str | None]:
    """Return (finding code or None, service version or None) from one bounded health probe."""
    options: dict[str, Any] = {"transport": transport} if transport is not None else {}
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=HEALTH_TIMEOUT_SECONDS, **options
        ) as client:
            response = client.get(
                base + "/api/v1/health", headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.ConnectError:
        return "SERVICE_DOWN", None
    except httpx.TimeoutException:
        # Loopback either refuses at once or accepts; a timeout means the process is there
        # but not serving, whether the accept backlog or the handler is stuck.
        return "SERVICE_HUNG", None
    except Exception:  # noqa: BLE001 - never include credentials, URLs or HTTP exception details.
        return "SERVICE_UNAVAILABLE", None
    if response.status_code in {401, 403}:
        return "SERVICE_AUTH_REJECTED", None
    if response.is_redirect:
        # Our service never redirects health; something else answered. Not followed.
        return "SERVICE_UNAVAILABLE", None
    try:
        response.raise_for_status()
        health = response.json()
    except (httpx.HTTPError, ValueError):
        return "SERVICE_UNHEALTHY", None
    if (
        isinstance(health, dict)
        and health.get("status") == "ok"
        and isinstance(health.get("version"), str)
    ):
        return None, health["version"]
    return "SERVICE_UNHEALTHY", None


def port_from_base(base: str) -> int:
    match = re.search(r":(\d{1,5})$", base)
    return int(match.group(1)) if match else DEFAULT_PORT


def parse_netstat(text: str, port: int) -> dict[str, Any]:
    """Read listener PID and CLOSE_WAIT count for one local port from `netstat -ano` output."""
    listener: int | None = None
    close_wait = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, state, pid = parts[1], parts[3].upper(), parts[4]
        if not local.endswith(f":{port}") or not pid.isdigit():
            continue
        if state == "LISTENING":
            listener = int(pid)
        elif state == "CLOSE_WAIT":
            close_wait += 1
    evidence: dict[str, Any] = {"close_wait_sockets": close_wait}
    if listener is not None:
        evidence["listener_pid"] = listener
    return evidence


def parse_sc_pid(text: str) -> int | None:
    match = re.search(r"^\s*PID\s*:\s*(\d+)", text, re.MULTILINE)
    pid = int(match.group(1)) if match else None
    return pid or None


def parse_sc_state(text: str) -> str | None:
    match = re.search(r"STATE\s*:\s*\d+\s+(\w+)", text)
    return match.group(1) if match else None


def _process_details(pid: int, runner: Runner) -> dict[str, Any]:
    """Parent PID and start time of one process; empty when it cannot be read."""
    script = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}';"
        "if($p){'{0}|{1}' -f $p.ParentProcessId,$p.CreationDate.ToUniversalTime().ToString('o')}"
    )
    shell = "pwsh" if shutil.which("pwsh") else "powershell"
    result = runner([shell, "-NoProfile", "-NonInteractive", "-Command", script])
    if result is None or result.returncode != 0 or "|" not in result.stdout:
        return {}
    parent, started = result.stdout.strip().split("|", 1)
    details: dict[str, Any] = {"listener_started_utc": started}
    if parent.isdigit():
        details["listener_parent_pid"] = int(parent)
    return details


def collect_evidence(port: int, *, runner: Runner = _run) -> dict[str, Any]:
    """Collect safe, read-only process evidence for the service port. Missing facts are omitted."""
    evidence: dict[str, Any] = {"port": port}
    if sys.platform != "win32":
        result = runner(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        if result is not None and result.stdout.strip().splitlines():
            first = result.stdout.strip().splitlines()[0]
            if first.isdigit():
                evidence["listener_pid"] = int(first)
        return evidence
    netstat = runner(["netstat", "-ano", "-p", "TCP"])
    if netstat is not None and netstat.returncode == 0:
        evidence.update(parse_netstat(netstat.stdout, port))
    sc = runner(["sc", "queryex", SERVICE_NAME])
    if sc is not None and sc.returncode == 0:
        evidence["scm_registered"] = True
        state = parse_sc_state(sc.stdout)
        if state:
            evidence["scm_state"] = state
        scm_pid = parse_sc_pid(sc.stdout)
        if scm_pid:
            evidence["scm_pid"] = scm_pid
    listener = evidence.get("listener_pid")
    if isinstance(listener, int):
        evidence.update(_process_details(listener, runner))
    return evidence


def owned_by_service(evidence: dict[str, Any]) -> bool | None:
    """Whether the port listener is the SCM service or its child; None when unknown."""
    scm_pid = evidence.get("scm_pid")
    listener = evidence.get("listener_pid")
    if not isinstance(scm_pid, int) or not isinstance(listener, int):
        return None
    return listener == scm_pid or evidence.get("listener_parent_pid") == scm_pid


def is_elevated() -> bool:
    if sys.platform == "win32":
        import ctypes

        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False
    geteuid = getattr(__import__("os"), "geteuid", None)
    return bool(geteuid is not None and geteuid() == 0)


def build_finding(code: str, *, host: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Attach diagnosis, evidence and the one proposed fix to a service finding code."""
    fix_id = _FIX_FOR_CODE[code]
    scm = bool(evidence.get("scm_registered"))
    if _scm_alive_without_server(code, evidence):
        # `sc start` would answer "already running"; the SCM host must be cycled.
        fix_id = "service-restart"
    if fix_id == "none":
        listener = evidence.get("listener_pid")
        command = f"Stop process {listener} yourself, then run sqlctx doctor check --host {host}"
    else:
        command = f"sqlctx doctor fix --host {host} --finding {code}"
    if code == "SERVICE_HUNG" and evidence.get("close_wait_sockets"):
        diagnosis = (
            _DIAGNOSES[code]
            + f" {evidence['close_wait_sockets']} client socket(s) are left in CLOSE_WAIT, so"
            " the service has stopped reading requests."
        )
    elif _scm_alive_without_server(code, evidence):
        diagnosis = (
            f"Windows reports the SCM service {evidence['scm_state']} (PID "
            f"{evidence.get('scm_pid', 'unknown')}), but its server process is gone and nothing"
            " listens on the service port."
        )
    else:
        diagnosis = _DIAGNOSES[code]
    finding: dict[str, Any] = {
        "code": code,
        "action": f"Run: {command}" if fix_id != "none" else command,
        "diagnosis": diagnosis,
        "evidence": evidence,
        "proposed_fix": {
            "fix_id": fix_id,
            "command": command,
            # The SCM service and its process can only be controlled with Administrator rights.
            "requires_elevation": scm and fix_id in {"service-start", "service-restart"},
            "effect": _FIX_EFFECTS[fix_id],
        },
    }
    return finding


def diagnose_service(
    metadata: object, *, host: str, transport: httpx.BaseTransport | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (service findings, service version). Read-only."""
    from sqlctx.security.transport import agent_connection

    try:
        base, token = agent_connection(metadata)
    except SqlCtxError:
        return [build_finding("SERVICE_METADATA_INVALID", host=host, evidence={})], None
    code, version = probe_health(base, token, transport=transport)
    if code is None:
        return [], version
    evidence = collect_evidence(port_from_base(base))
    if code in {"SERVICE_HUNG", "SERVICE_UNAVAILABLE"} and owned_by_service(evidence) is False:
        code = "PORT_OWNER_MISMATCH"
    if code == "SERVICE_DOWN" and isinstance(evidence.get("listener_pid"), int):
        # Refused yet something listens: the probe raced a restart; treat it as stuck.
        code = "SERVICE_HUNG"
    return [build_finding(code, host=host, evidence=evidence)], version


def _scm_alive_without_server(code: str, evidence: dict[str, Any]) -> bool:
    return code == "SERVICE_DOWN" and evidence.get("scm_state") in {"RUNNING", "STOP_PENDING"}


class FixNeedsElevation(SqlCtxError):
    def __init__(self, host: str, code: str) -> None:
        super().__init__(
            "DOCTOR_FIX_NEEDS_ELEVATION",
            "This fix needs Administrator rights. In an Administrator PowerShell run: "
            f"sqlctx doctor fix --host {host} --finding {code}",
        )


def _sc(action: str, runner: Runner) -> subprocess.CompletedProcess[str]:
    result = runner(["sc", action, SERVICE_NAME])
    if result is None:
        raise SqlCtxError("DOCTOR_FIX_FAILED", f"sc {action} could not run.")
    return result


def _access_denied(result: subprocess.CompletedProcess[str]) -> bool:
    # sc and taskkill both report ERROR_ACCESS_DENIED as 5 or in their message text.
    return result.returncode == 5 or "access is denied" in (result.stdout + result.stderr).lower()


def _wait_until(predicate: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def apply_windows_scm_fix(
    finding: dict[str, Any], *, host: str, runner: Runner = _run
) -> list[str]:
    """Apply service-start or service-restart against the SCM service. Returns the steps taken."""
    code = finding["code"]
    fix_id = finding["proposed_fix"]["fix_id"]
    port = int(finding.get("evidence", {}).get("port", DEFAULT_PORT))
    steps: list[str] = []
    if fix_id == "service-restart":
        stop = _sc("stop", runner)
        if _access_denied(stop):
            raise FixNeedsElevation(host, code)
        steps.append("requested SCM stop")

        def scm_stopped() -> bool:
            return parse_sc_state(_sc("query", runner).stdout) == "STOPPED"

        if not _wait_until(scm_stopped, _STOP_WAIT_SECONDS):
            # Re-read rather than trust the earlier PIDs: only kill what is still ours.
            current = collect_evidence(port, runner=runner)
            targets: list[int] = []
            if "listener_pid" in current:
                if owned_by_service(current) is not True:
                    raise SqlCtxError(
                        "DOCTOR_FIX_REFUSED",
                        "The port is still held by a process that is not the SQL Context Pack "
                        "service; doctor will not stop it.",
                    )
                targets.append(current["listener_pid"])
            if isinstance(current.get("scm_pid"), int):
                targets.append(current["scm_pid"])
            for pid in dict.fromkeys(targets):
                kill = runner(["taskkill", "/PID", str(pid), "/T", "/F"])
                if kill is not None and _access_denied(kill):
                    raise FixNeedsElevation(host, code)
                steps.append(f"force-stopped stuck service process {pid}")
            if not _wait_until(scm_stopped, _STOP_WAIT_SECONDS):
                raise SqlCtxError(
                    "DOCTOR_FIX_FAILED", "The service did not reach STOPPED after force-stop."
                )
    start = _sc("start", runner)
    if _access_denied(start):
        raise FixNeedsElevation(host, code)
    # 1056 = already running; harmless when a restart raced the SCM recovery action.
    if start.returncode not in {0, 1056}:
        raise SqlCtxError("DOCTOR_FIX_FAILED", f"sc start returned {start.returncode}.")
    steps.append("started SCM service")
    return steps


def apply_portable_fix(finding: dict[str, Any], *, port: int) -> list[str]:
    """Apply service-start or service-restart through the non-SCM supervisor."""
    from sqlctx.service.manager import detect_host_os, manage

    python = Path(sys.executable).resolve()
    host_os = detect_host_os()
    steps: list[str] = []
    if finding["proposed_fix"]["fix_id"] == "service-restart":
        manage("stop", python=python, port=port, host_os=host_os)
        steps.append("stopped service")
    result = manage("start", python=python, port=port, host_os=host_os)
    steps.append(f"start returned {result.get('status')}")
    return steps
