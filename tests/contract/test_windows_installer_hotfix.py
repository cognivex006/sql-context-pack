"""Windows installer regressions found while updating a real machine to 3.5.4."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sqlctx.core.errors import SqlCtxError
from sqlctx.security import service_access
from sqlctx.security.service_access import SERVICE_ACCOUNT, _service_grant_present

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "python-preflight.ps1"


def test_preflight_probe_passes_no_double_quotes_to_python() -> None:
    # Windows PowerShell 5.1 passes embedded double quotes to native programs unescaped.
    text = PREFLIGHT.read_text(encoding="utf-8")
    probes = re.findall(r"'-c',\s*'((?:[^']|'')*)'", text)
    assert probes, "the preflight Python probe was not found"
    assert all('"' not in probe for probe in probes)


@pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="Windows PowerShell 5.1 is only available on Windows",
)
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_preflight_finds_python_under_each_windows_powershell(shell: str) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    result = subprocess.run(  # noqa: S603 - fixed shell and repository script.
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PREFLIGHT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["status"] == "ready"


def _listing(*aces: str) -> Any:
    lines = [f"C:\\work\\sql {aces[0]}" if aces else "C:\\work\\sql"]
    lines += [f"         {ace}" for ace in aces[1:]]
    lines += ["", "Successfully processed 1 files; Failed processing 0 files"]

    def run(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), 0, "\n".join(lines), "")

    return run


@pytest.mark.parametrize(
    ("aces", "present"),
    [
        ((f"{SERVICE_ACCOUNT}:(OI)(CI)(M)", "BUILTIN\\Administrators:(OI)(CI)(F)"), True),
        (("BUILTIN\\Administrators:(OI)(CI)(F)", f"{SERVICE_ACCOUNT}:(OI)(CI)(F)"), True),
        ((f"{SERVICE_ACCOUNT}:(I)(OI)(CI)(M)",), True),
        ((f"{SERVICE_ACCOUNT}:(OI)(CI)(RX)",), False),
        ((f"{SERVICE_ACCOUNT}:(M)",), False),
        ((f"{SERVICE_ACCOUNT}:(OI)(CI)(M)", f"{SERVICE_ACCOUNT}:(OI)(CI)(DENY)(W)"), False),
        (("BUILTIN\\Administrators:(OI)(CI)(F)",), False),
    ],
)
def test_existing_service_grant_is_recognized(
    monkeypatch: pytest.MonkeyPatch, aces: tuple[str, ...], present: bool
) -> None:
    monkeypatch.setattr(service_access.subprocess, "run", _listing(*aces))
    assert _service_grant_present("icacls", Path("C:/work/sql")) is present


def test_a_failed_listing_is_never_treated_as_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), 5, "", "Access is denied.")

    monkeypatch.setattr(service_access.subprocess, "run", run)
    assert _service_grant_present("icacls", Path("C:/work/sql")) is False


def _grant_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, granted: bool, grant_code: int
) -> list[list[str]]:
    calls: list[list[str]] = []
    ace = f"{SERVICE_ACCOUNT}:(OI)(CI)(M)" if granted else "BUILTIN\\Users:(OI)(CI)(RX)"

    def run(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        if "/grant" in command:
            return subprocess.CompletedProcess(list(command), grant_code, "", "Access is denied.")
        return subprocess.CompletedProcess(list(command), 0, f"{command[1]} {ace}\n", "")

    monkeypatch.setattr(service_access.os, "name", "nt")
    monkeypatch.setattr(service_access.shutil, "which", lambda _: "icacls")
    monkeypatch.setattr(service_access.subprocess, "run", run)
    monkeypatch.setenv("WINDIR", str(tmp_path / "windows"))
    for name in ("ProgramFiles", "ProgramFiles(x86)", "PROGRAMDATA"):
        monkeypatch.delenv(name, raising=False)
    return calls


def test_an_existing_grant_is_not_reapplied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _grant_calls(monkeypatch, tmp_path, granted=True, grant_code=5)
    service_access.grant_folder_access(tmp_path / "in", tmp_path / "out")
    assert calls and all("/grant" not in call for call in calls)


def test_a_missing_grant_is_applied_and_its_failure_still_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _grant_calls(monkeypatch, tmp_path, granted=False, grant_code=5)
    with pytest.raises(SqlCtxError) as error:
        service_access.grant_folder_access(tmp_path / "in", tmp_path / "out")
    assert error.value.code == "SERVICE_FOLDER_ACCESS_FAILED"
    assert any("/grant" in call for call in calls)
