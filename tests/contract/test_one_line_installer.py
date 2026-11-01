from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installer"
CANONICAL_URL = "https://github.com/cognivex006/sql-context-pack"
REPOSITORY_PATH = "/cognivex006/sql-context-pack"
TAG = "v9.9.9"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
SH = shutil.which("sh")
SPEC = importlib.util.spec_from_file_location(
    "build_release_assets", ROOT / "scripts/build_release_assets.py"
)
assert SPEC and SPEC.loader
build_release_assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_release_assets)


def _text(name: str) -> str:
    return (INSTALLER / name).read_bytes().decode("ascii").replace("\r\n", "\n")


def test_update_scripts_differ_from_install_only_in_operation() -> None:
    assert _text("update.ps1") == _text("install.ps1").replace(
        "\n$operation = 'install'\n", "\n$operation = 'update'\n", 1
    )
    assert _text("update.sh") == _text("install.sh").replace(
        "\nOPERATION=install\n", "\nOPERATION=update\n", 1
    )


def test_bootstraps_are_ascii_and_target_only_the_canonical_release() -> None:
    for name in build_release_assets.BOOTSTRAPS:
        data = (INSTALLER / name).read_bytes()
        # Windows PowerShell 5.1 decodes `irm` output without a charset.
        assert data.isascii(), name
        assert not data.startswith(b"\xef\xbb\xbf"), name
        if name.endswith(".sh"):
            assert b"\r" not in data, name
        text = data.decode("ascii")
        assert text.count(CANONICAL_URL) >= 2, name
        assert "releases/latest" in text, name
        assert "sql-context-pack-install.zip" in text, name


def test_posix_bootstrap_shell_and_embedded_python_parse() -> None:
    text = _text("install.sh")
    body = text.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
    compile(body, "installer/install.sh", "exec")
    if SH is not None:
        result = subprocess.run(  # noqa: S603 - resolved sh and a repository script.
            [SH, "-n", str(INSTALLER / "install.sh")], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not available")
def test_windows_bootstrap_parses() -> None:
    command = (
        "$tokens = $null; $errors = $null; [void][Management.Automation.Language.Parser]::ParseFile("
        f"'{INSTALLER / 'install.ps1'}', [ref]$tokens, [ref]$errors); "
        "$errors | ForEach-Object { $_.Message }; exit $errors.Count"
    )
    result = subprocess.run(  # noqa: S603 - resolved PowerShell and a fixed parse command.
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _bundle(path: Path, version: str) -> bytes:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(".codex-plugin/plugin.json", json.dumps({"version": version}))
        for name in (
            "install.ps1",
            "install.sh",
            "scripts/install-global.ps1",
            "scripts/global_install.py",
            "scripts/bootstrap.py",
        ):
            bundle.writestr(name, "# placeholder\n")
    return path.read_bytes()


class _Release(http.server.BaseHTTPRequestHandler):
    assets: dict[str, bytes] = {}

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        port = self.server.server_address[1]
        if self.path == f"{REPOSITORY_PATH}/releases/latest":
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{port}{REPOSITORY_PATH}/releases/tag/{TAG}"
            )
            self.end_headers()
            return
        prefix = f"{REPOSITORY_PATH}/releases/download/{TAG}/"
        data = self.assets.get(self.path[len(prefix) :]) if self.path.startswith(prefix) else None
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def release(tmp_path: Path) -> Iterator[tuple[str, dict[str, bytes]]]:
    data = _bundle(tmp_path / "bundle.zip", TAG[1:])
    assets = {
        "sql-context-pack-install.zip": data,
        "sql-context-pack-install.zip.sha256": build_release_assets.checksum_line(
            "sql-context-pack-install.zip", data
        ).encode(),
    }
    handler = type("Release", (_Release,), {"assets": assets})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}{REPOSITORY_PATH}", assets
    finally:
        server.shutdown()
        server.server_close()


def _environment(tmp_path: Path, hosts: str) -> dict[str, str]:
    home, local, temp = tmp_path / "home", tmp_path / "local", tmp_path / "temp"
    for folder in (home, local, temp):
        folder.mkdir(exist_ok=True)
    claude = home / ".claude" / "plugins"
    claude.mkdir(parents=True, exist_ok=True)
    # Claude already lists the plugin from another marketplace; setup must leave it there.
    (claude / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"sql-context-pack@sql-context-pack": []}}),
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SQLCTX_", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_"))
    }
    environment.update(
        {
            "USERPROFILE": str(home),
            "HOME": str(home),
            "LOCALAPPDATA": str(local),
            "XDG_DATA_HOME": str(local),
            "TEMP": str(temp),
            "TMP": str(temp),
            "TMPDIR": str(temp),
            "SQLCTX_HOST": hosts,
            "SQLCTX_DRY_RUN": "1",
        }
    )
    return environment


def _run(tmp_path: Path, name: str, base_url: str, hosts: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / name
    script.write_text(_text(name).replace(CANONICAL_URL, base_url), encoding="ascii")
    if name.endswith(".ps1"):
        command = [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Get-Content -Raw -LiteralPath '{script}' | Invoke-Expression",
        ]
    else:
        command = [str(SH), script.as_posix()]
    result = subprocess.run(  # noqa: S603 - resolved shell and a generated test copy.
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=_environment(tmp_path, hosts),
        cwd=tmp_path,
    )
    # A dry run downloads into the temp folder only, and always removes it.
    assert not any((tmp_path / "temp").iterdir())
    assert not (tmp_path / "local" / "Programs").exists()
    assert not (tmp_path / "local" / "sql-context-pack").exists()
    return result


def _bootstraps() -> list[object]:
    names: list[object] = []
    names.append(
        pytest.param(
            "install.ps1",
            marks=pytest.mark.skipif(
                POWERSHELL is None or sys.platform != "win32", reason="Windows PowerShell only"
            ),
        )
    )
    names.append(pytest.param("install.sh", marks=pytest.mark.skipif(SH is None, reason="no sh")))
    return names


@pytest.mark.parametrize("name", _bootstraps())
def test_dry_run_verifies_release_and_keeps_foreign_registration(
    tmp_path: Path, release: tuple[str, dict[str, bytes]], name: str
) -> None:
    base_url, _ = release
    result = _run(tmp_path, name, base_url, "codex,claude,agy")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"Dry run: SQL Context Pack {TAG[1:]} ({TAG}) downloaded and verified" in output
    assert "codex: install package, service and plugin sql-context-pack@personal" in output
    assert (
        "claude: package and service only; the plugin stays "
        "sql-context-pack@sql-context-pack from its own marketplace"
    ) in output
    assert "agy: install package, service, skill files and MCP entry" in output


@pytest.mark.parametrize("name", _bootstraps())
def test_checksum_mismatch_stops_before_anything_is_unpacked(
    tmp_path: Path, release: tuple[str, dict[str, bytes]], name: str
) -> None:
    base_url, assets = release
    assets["sql-context-pack-install.zip.sha256"] = (
        hashlib.sha256(b"other").hexdigest() + "  sql-context-pack-install.zip\n"
    ).encode()
    result = _run(tmp_path, name, base_url, "codex")
    assert result.returncode != 0
    assert "Release checksum mismatch" in result.stdout + result.stderr


@pytest.mark.parametrize("name", _bootstraps())
def test_release_without_assets_is_reported_plainly(
    tmp_path: Path, release: tuple[str, dict[str, bytes]], name: str
) -> None:
    base_url, assets = release
    assets.clear()
    result = _run(tmp_path, name, base_url, "codex")
    assert result.returncode != 0
    assert "has no sql-context-pack-install.zip" in result.stdout + result.stderr


@pytest.mark.parametrize("name", _bootstraps())
def test_unknown_host_is_refused(
    tmp_path: Path, release: tuple[str, dict[str, bytes]], name: str
) -> None:
    base_url, _ = release
    result = _run(tmp_path, name, base_url, "codex,cursor")
    assert result.returncode != 0
    assert "SQLCTX_HOST must name codex, claude, gemini or agy" in result.stdout + result.stderr


def test_checksum_line_matches_the_bootstrap_format() -> None:
    line = build_release_assets.checksum_line("sql-context-pack-install.zip", b"data")
    assert line == hashlib.sha256(b"data").hexdigest() + "  sql-context-pack-install.zip\n"


def test_release_assets_are_built_from_the_tag_with_lf_bytes(tmp_path: Path) -> None:
    try:
        tags = build_release_assets._git("tag", "--list", "v*", "--sort=-v:refname").split()
        build_release_assets._git("cat-file", "-e", "HEAD:installer/install.sh")
    except build_release_assets.AssetError:
        tags = []
    if not tags:
        pytest.skip("needs a release tag and committed installer/ scripts")
    tags = [tag.decode() for tag in tags]
    files = build_release_assets.build(tags[0], tmp_path / "assets")
    names = [path.name for path in files]
    assert names == [
        "sql-context-pack-install.zip",
        "sql-context-pack-install.zip.sha256",
        *build_release_assets.BOOTSTRAPS,
        "SHA256SUMS",
    ]
    bundle = tmp_path / "assets" / "sql-context-pack-install.zip"
    checksum = (tmp_path / "assets" / "sql-context-pack-install.zip.sha256").read_text("ascii")
    assert checksum == build_release_assets.checksum_line(bundle.name, bundle.read_bytes())
    with zipfile.ZipFile(bundle) as archive:
        assert b"\r\n" not in archive.read("install.sh")
        manifest = json.loads(archive.read(".codex-plugin/plugin.json"))
    assert manifest["version"] == tags[0][1:]
