from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
windows_powershell = pytest.mark.skipif(
    POWERSHELL is None or sys.platform != "win32", reason="Windows PowerShell only"
)


def test_plugin_bundles_session_bridge_and_start_hook() -> None:
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))

    assert manifest["mcpServers"] == "./.mcp.json"
    assert "hooks" not in manifest
    assert mcp["mcpServers"]["sql-context-pack"]["command"] == "sqlctx-mcp-bridge"
    assert "SessionStart" in hooks["hooks"]


def test_windows_service_script_is_loopback_only_and_transactional() -> None:
    installer = (ROOT / "scripts/windows-service.ps1").read_text(encoding="utf-8")
    host = (ROOT / "scripts/sqlctx_windows_service.py").read_text(encoding="utf-8")

    assert "127.0.0.1" in installer and "127.0.0.1" in host
    assert "New-NetFirewallRule" not in installer
    assert "obj= LocalSystem" not in installer
    assert "NT SERVICE\\SQLContextPack" in installer
    assert "Set-ProtectedAcl $appRoot" in installer
    assert "Set-ProtectedAcl $interpreterRoot" in installer
    assert "stage_service_python.py" in installer
    assert "-I -B" in installer
    assert "service-config.json" in installer
    assert "$installTarget" in installer
    assert "profile list" in installer
    assert "backupApp" in installer
    assert "Service health verification failed" in installer
    assert "PORT_IN_USE" in installer
    assert "health.version -eq $expectedVersion" in installer
    assert "connection-metadata.backup.json" in installer
    assert "New-Item -ItemType Directory -Path $stageRoot" in installer
    assert "Remove-StaleTransactions" in installer
    assert "service-child.log" in installer
    assert "Remove-Item -LiteralPath $appRoot" in installer
    assert "$hostScript, $serviceConfig, $installState" in installer
    assert "config and runtime data were preserved" in installer
    assert "SQLCTX_OWNER_ACCOUNT" in host
    assert 'runtime_root / "service-child.log"' in host
    assert "stderr=subprocess.STDOUT" in host
    assert 'app_root / "win32/lib"' in host
    assert 'app_root / "pywin32_system32"' in host


def test_update_and_dev_check_cover_all_required_surfaces() -> None:
    root_installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
    global_installer = (ROOT / "scripts/install-global.ps1").read_text(encoding="utf-8")
    dev_check = (ROOT / "scripts/dev-check.ps1").read_text(encoding="utf-8")

    assert "windows-service.ps1" in root_installer
    assert "[switch]$Update" in root_installer
    assert "[switch]$Repair" in root_installer
    assert "install-owner-package-active.py" in global_installer
    assert "Get-Process -Name 'sqlctx-mcp-bridge'" in global_installer
    assert "sqlctx-mcp-bridge.exe" in global_installer
    assert "dependency_fingerprint" in global_installer
    assert "pip installation skipped" in global_installer
    assert "--no-deps" in global_installer
    assert "PackageArtifact" in root_installer
    assert "pip wheel --no-deps" in root_installer
    assert "wheel build skipped" in root_installer
    assert "service restart skipped" in (ROOT / "scripts/windows-service.ps1").read_text(
        encoding="utf-8"
    )
    assert "installed_package_fingerprint" in (ROOT / "scripts/windows-service.ps1").read_text(
        encoding="utf-8"
    )
    assert "UAC and service restart skipped" in (ROOT / "scripts/windows-service.ps1").read_text(
        encoding="utf-8"
    )
    lifecycle = (ROOT / "scripts/lifecycle.ps1").read_text(encoding="utf-8")
    assert "-NativePlugin" in lifecycle
    assert "SkipPluginInstall = $NativePlugin" in root_installer
    assert "Native marketplace owns plugin files" in global_installer
    assert "-Operation remove" in lifecycle
    assert "pip uninstall --yes sql-context-pack" in lifecycle
    assert "plugin remove 'sql-context-pack@sql-context-pack'" in lifecycle
    assert "plugin uninstall 'sql-context-pack@sql-context-pack'" in lifecycle
    assert "extensions uninstall 'sql-context-pack'" in lifecycle
    for residue in (
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".egg-info",
    ):
        assert residue in dev_check
    assert "finally" in dev_check


def _sandbox(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A source root whose helper scripts are stand-ins, plus an environment whose owner and
    service install state already match their fingerprint, so no wheel, pip or service work runs.
    """
    source = tmp_path / "source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    executable = sys.executable.replace("'", "''")
    (scripts / "python-preflight.ps1").write_text(
        f"[pscustomobject]@{{ status = 'ready'; executable = '{executable}' }} "
        "| ConvertTo-Json -Compress\nexit 0\n",
        encoding="utf-8",
    )
    fingerprint = {
        key: "test"
        for key in ("app_fingerprint", "package_fingerprint", "dependency_fingerprint", "python")
    }
    (scripts / "install_fingerprint.py").write_text(
        f"print({json.dumps(json.dumps(fingerprint))})\n", encoding="utf-8"
    )
    local, program_data = tmp_path / "local", tmp_path / "programdata"
    for state in (
        local / "sql-context-pack/install-state.json",
        program_data / "SQLContextPack/install-state.json",
    ):
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(fingerprint), encoding="utf-8")
    environment = dict(os.environ)
    environment.update({"LOCALAPPDATA": str(local), "PROGRAMDATA": str(program_data)})
    return source, environment


def _powershell(
    script: Path, arguments: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - resolved PowerShell and a generated test copy.
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        + arguments,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=environment,
    )


@windows_powershell
@pytest.mark.parametrize(
    ("harness", "mode"),
    [("codex", "plugin"), ("claude", "plugin"), ("gemini", "plugin"), ("agy", "skill")],
)
def test_root_installer_selects_the_layout_each_host_loads(
    tmp_path: Path, harness: str, mode: str
) -> None:
    """`sqlctx doctor update --host <host>` runs exactly `install.ps1 -Update -Harness <host>`."""
    source, environment = _sandbox(tmp_path)
    shutil.copy2(ROOT / "install.ps1", source / "install.ps1")
    real = (ROOT / "scripts/install-global.ps1").read_text(encoding="utf-8")
    param_block, separator, _ = real.partition("\n)\n")
    assert separator and param_block.startswith("[CmdletBinding()]\nparam(")
    record = tmp_path / "install-global.json"
    # The real parameter block stays, so its ValidateSet binds exactly as in a release.
    (source / "scripts/install-global.ps1").write_text(
        param_block
        + separator
        + "@{ Operation = $Operation; Mode = $Mode; Harness = $Harness } | ConvertTo-Json "
        + f"| Set-Content -LiteralPath '{record}'\nexit 0\n",
        encoding="utf-8",
    )
    (source / "scripts/grant_service_folders.py").write_text("", encoding="utf-8")
    (source / "scripts/windows-service.ps1").write_text(
        "param($Operation, $SourceRoot, $PythonExecutable, $PackageArtifact)\nexit 0\n",
        encoding="utf-8",
    )

    result = _powershell(
        source / "install.ps1", ["-Update", "-SkipConfigure", "-Harness", harness], environment
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(record.read_text(encoding="utf-8-sig")) == {
        "Operation": "update",
        "Mode": mode,
        "Harness": harness,
    }


@windows_powershell
def test_global_installer_hands_agy_the_skill_layout(tmp_path: Path) -> None:
    source, environment = _sandbox(tmp_path)
    shutil.copy2(ROOT / "scripts/install-global.ps1", source / "scripts/install-global.ps1")
    record = tmp_path / "global_install.json"
    (source / "scripts/global_install.py").write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )

    # The package and PATH steps write the owner's real user profile and run the installed
    # launchers; they do not affect the arguments handed to global_install.py.
    result = _powershell(
        source / "scripts/install-global.ps1",
        ["-Operation", "update", "-Mode", "skill", "-Harness", "agy"]
        + ["-SkipPackageInstall", "-SkipPathUpdate"],
        environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads(record.read_text(encoding="utf-8"))
    assert arguments[:-1] == ["update", "--mode", "skill", "--harness", "agy", "--source-root"]
    assert Path(arguments[-1]).resolve() == source.resolve()
