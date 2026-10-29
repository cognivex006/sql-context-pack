from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.core.errors import SqlCtxError
from sqlctx.service_diagnosis import (
    FixNeedsElevation,
    apply_windows_scm_fix,
    build_finding,
    diagnose_service,
    owned_by_service,
    parse_netstat,
    parse_sc_pid,
    parse_sc_state,
    probe_health,
)

BASE = "http://127.0.0.1:8765"
TOKEN = "agent-token-not-real"
METADATA = {"mcp_url": BASE + "/mcp", "agent_token": TOKEN}

NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       8372
  TCP    127.0.0.1:8765         127.0.0.1:54931        CLOSE_WAIT      8372
  TCP    127.0.0.1:8765         127.0.0.1:56886        ESTABLISHED     8372
  TCP    127.0.0.1:18765        0.0.0.0:0              LISTENING       9999
"""

SC_RUNNING = """
SERVICE_NAME: SQLContextPack
        TYPE               : 10  WIN32_OWN_PROCESS
        STATE              : 4  RUNNING
        PID                : 6788
"""


def _raising(exc: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def _responding(status: int, body: object) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        (_raising(httpx.ConnectError("refused")), ("SERVICE_DOWN", None)),
        (_raising(httpx.ReadTimeout("slow")), ("SERVICE_HUNG", None)),
        (_raising(httpx.ConnectTimeout("backlog")), ("SERVICE_HUNG", None)),
        (_responding(401, {}), ("SERVICE_AUTH_REJECTED", None)),
        (_responding(500, {}), ("SERVICE_UNHEALTHY", None)),
        (_responding(200, {"status": "degraded"}), ("SERVICE_UNHEALTHY", None)),
        (_responding(200, {"status": "ok", "version": "3.4.0"}), (None, "3.4.0")),
    ],
)
def test_probe_health_classifies_each_fault(
    transport: httpx.MockTransport, expected: tuple[str | None, str | None]
) -> None:
    assert probe_health(BASE, TOKEN, transport=transport) == expected


def test_netstat_and_sc_parsing() -> None:
    assert parse_netstat(NETSTAT, 8765) == {"listener_pid": 8372, "close_wait_sockets": 1}
    assert parse_sc_pid(SC_RUNNING) == 6788
    assert parse_sc_state(SC_RUNNING) == "RUNNING"
    assert parse_sc_pid("        PID                : 0\n") is None


def test_listener_ownership() -> None:
    assert owned_by_service({"scm_pid": 6788, "listener_pid": 8372, "listener_parent_pid": 6788})
    assert owned_by_service({"scm_pid": 6788, "listener_pid": 6788}) is True
    assert owned_by_service({"scm_pid": 6788, "listener_pid": 4242}) is False
    assert owned_by_service({"listener_pid": 8372}) is None


def test_hung_finding_carries_diagnosis_evidence_and_fix(monkeypatch: Any) -> None:
    evidence = {
        "port": 8765,
        "listener_pid": 8372,
        "listener_parent_pid": 6788,
        "scm_pid": 6788,
        "scm_registered": True,
        "close_wait_sockets": 1,
    }
    monkeypatch.setattr("sqlctx.service_diagnosis.collect_evidence", lambda port: dict(evidence))
    findings, version = diagnose_service(
        METADATA, host="codex", transport=_raising(httpx.ReadTimeout("slow"))
    )
    assert version is None
    [finding] = findings
    assert finding["code"] == "SERVICE_HUNG"
    assert "CLOSE_WAIT" in finding["diagnosis"]
    assert finding["evidence"]["listener_pid"] == 8372
    assert finding["proposed_fix"] == {
        "fix_id": "service-restart",
        "command": "sqlctx doctor fix --host codex --finding SERVICE_HUNG",
        "requires_elevation": True,
        "effect": finding["proposed_fix"]["effect"],
    }
    # Evidence and text never echo the token or the service URL.
    rendered = json.dumps(finding)
    assert TOKEN not in rendered
    assert "http://" not in rendered


def test_foreign_port_owner_is_named_not_killed(monkeypatch: Any) -> None:
    evidence = {"port": 8765, "listener_pid": 4242, "scm_pid": 6788, "scm_registered": True}
    monkeypatch.setattr("sqlctx.service_diagnosis.collect_evidence", lambda port: dict(evidence))
    [finding], _ = diagnose_service(
        METADATA, host="claude", transport=_raising(httpx.ReadTimeout("slow"))
    )
    assert finding["code"] == "PORT_OWNER_MISMATCH"
    assert finding["proposed_fix"]["fix_id"] == "none"
    assert "4242" in finding["proposed_fix"]["command"]


def test_scm_running_without_server_proposes_restart(monkeypatch: Any) -> None:
    evidence = {"port": 8765, "scm_pid": 6788, "scm_registered": True, "scm_state": "RUNNING"}
    monkeypatch.setattr("sqlctx.service_diagnosis.collect_evidence", lambda port: dict(evidence))
    [finding], _ = diagnose_service(
        METADATA, host="codex", transport=_raising(httpx.ConnectError("refused"))
    )
    assert finding["code"] == "SERVICE_DOWN"
    assert "6788" in finding["diagnosis"]
    assert finding["proposed_fix"]["fix_id"] == "service-restart"


def test_invalid_metadata_is_its_own_finding() -> None:
    [finding], _ = diagnose_service({"mcp_url": "nope"}, host="codex")
    assert finding["code"] == "SERVICE_METADATA_INVALID"
    assert finding["proposed_fix"]["fix_id"] == "service-repair"


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_scm_restart_without_rights_reports_elevated_command() -> None:
    finding = build_finding(
        "SERVICE_HUNG", host="codex", evidence={"port": 8765, "scm_registered": True}
    )
    runner = lambda arguments: _completed(5)  # noqa: E731 - sc stop: access denied.
    with pytest.raises(FixNeedsElevation) as raised:
        apply_windows_scm_fix(finding, host="codex", runner=runner)
    assert "sqlctx doctor fix --host codex --finding SERVICE_HUNG" in raised.value.message


def test_scm_restart_force_stops_only_our_stuck_listener(monkeypatch: Any) -> None:
    monkeypatch.setattr("sqlctx.service_diagnosis._STOP_WAIT_SECONDS", 0.0)
    monkeypatch.setattr("sqlctx.service_diagnosis.sys.platform", "win32")
    calls: list[list[str]] = []
    killed = {"done": False}

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[0] == "netstat":
            return _completed(stdout="" if killed["done"] else NETSTAT)
        if arguments[0] == "taskkill":
            killed["done"] = True
            return _completed()
        if arguments[:2] == ["sc", "queryex"]:
            return _completed(stdout=SC_RUNNING)
        if arguments[:2] == ["sc", "query"]:
            return _completed(stdout="STATE : 1  STOPPED" if killed["done"] else SC_RUNNING)
        if arguments[0].startswith(("pwsh", "powershell")):
            return _completed(stdout="6788|2026-10-17T04:15:16Z")
        return _completed()

    finding = build_finding(
        "SERVICE_HUNG", host="codex", evidence={"port": 8765, "scm_registered": True}
    )
    steps = apply_windows_scm_fix(finding, host="codex", runner=runner)
    assert ["taskkill", "/PID", "8372", "/T", "/F"] in calls
    assert ["taskkill", "/PID", "6788", "/T", "/F"] in calls
    assert steps[-1] == "started SCM service"


def test_scm_restart_refuses_to_kill_a_foreign_listener(monkeypatch: Any) -> None:
    monkeypatch.setattr("sqlctx.service_diagnosis._STOP_WAIT_SECONDS", 0.0)
    monkeypatch.setattr("sqlctx.service_diagnosis.sys.platform", "win32")

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "netstat":
            return _completed(stdout=NETSTAT)
        if arguments[:2] in (["sc", "queryex"], ["sc", "query"]):
            return _completed(stdout=SC_RUNNING)
        if arguments[0].startswith(("pwsh", "powershell")):
            return _completed(stdout="1|2026-10-17T04:15:16Z")
        assert arguments[0] != "taskkill", "doctor killed a process it does not own"
        return _completed()

    finding = build_finding(
        "SERVICE_HUNG", host="codex", evidence={"port": 8765, "scm_registered": True}
    )
    with pytest.raises(SqlCtxError) as raised:
        apply_windows_scm_fix(finding, host="codex", runner=runner)
    assert raised.value.code == "DOCTOR_FIX_REFUSED"


def test_doctor_fix_with_nothing_wrong_changes_nothing(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        main,
        "inspect_installation",
        lambda **_: {"findings": [], "versions": {"service": "3.4.0"}},
    )
    result = CliRunner().invoke(main.app, ["doctor", "fix", "--host", "codex"])
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "nothing_to_fix"


def test_doctor_fix_verifies_before_reporting_success(monkeypatch: Any, tmp_path: Path) -> None:
    hung = build_finding(
        "SERVICE_DOWN", host="codex", evidence={"port": 8765, "scm_registered": False}
    )
    reports = iter(
        [
            {"findings": [hung], "versions": {"service": None}},
            {"findings": [], "versions": {"service": "3.4.0"}},
        ]
    )
    monkeypatch.setattr(main, "inspect_installation", lambda **_: next(reports))
    monkeypatch.setattr(
        "sqlctx.service_diagnosis.apply_portable_fix", lambda finding, port: ["started"]
    )
    result = CliRunner().invoke(main.app, ["doctor", "fix", "--host", "codex"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.splitlines()[-1])
    assert payload["status"] == "fixed"
    assert payload["before"] == ["SERVICE_DOWN"]


def test_finding_option_is_fix_only() -> None:
    result = CliRunner().invoke(main.app, ["doctor", "check", "--finding", "SERVICE_HUNG"])
    assert result.exit_code != 0
