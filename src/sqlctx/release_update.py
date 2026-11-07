"""Update from the published release when no install provenance exists.

A machine whose plugin came from a host's own marketplace has no `.sqlctx-install.json`, so
`doctor update` cannot find the checkout it was installed from. The release itself carries a
one-line updater that installs the package and service for exactly that layout. This module
fetches that updater from the requested release, checks it against the release's
`SHA256SUMS`, and runs it for one host. The updater installs only the latest release, so a
pinned version that is not the latest is refused rather than silently replaced.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from sqlctx.core.errors import SqlCtxError

REPOSITORY = "cognivex006/sql-context-pack"
_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
_DOWNLOAD = f"https://github.com/{REPOSITORY}/releases/download"
_ALLOWED_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

Fetch = Callable[[str], bytes]


def _fetch(url: str) -> bytes:
    with httpx.Client(trust_env=False, follow_redirects=True, timeout=30) as client:
        response = client.get(url)
        if response.url.host not in _ALLOWED_HOSTS | {"api.github.com"}:
            raise SqlCtxError("UPDATE_RELEASE_UNAVAILABLE", "A release download left GitHub.")
        response.raise_for_status()
        return response.content


def latest_release_tag(fetch: Fetch = _fetch) -> str:
    import json

    try:
        tag = json.loads(fetch(_API)).get("tag_name")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise SqlCtxError(
            "UPDATE_RELEASE_UNAVAILABLE", "The latest release could not be read from GitHub."
        ) from exc
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise SqlCtxError("UPDATE_RELEASE_UNAVAILABLE", "The latest release has no version tag.")
    return tag


def updater_name() -> str:
    return "update.ps1" if sys.platform == "win32" else "update.sh"


def fetch_verified_updater(tag: str, directory: Path, fetch: Fetch = _fetch) -> Path:
    """Download the release updater and accept it only if `SHA256SUMS` lists its digest."""
    name = updater_name()
    try:
        sums = fetch(f"{_DOWNLOAD}/{tag}/SHA256SUMS").decode("ascii")
        script = fetch(f"{_DOWNLOAD}/{tag}/{name}")
    except (httpx.HTTPError, UnicodeDecodeError) as exc:
        raise SqlCtxError(
            "UPDATE_RELEASE_UNAVAILABLE",
            f"Release {tag} has no one-line updater assets; no installation was started.",
        ) from exc
    expected = {
        parts[-1].lstrip("*"): parts[0].lower()
        for parts in (line.split() for line in sums.splitlines())
        if len(parts) >= 2
    }.get(name)
    if expected is None or hashlib.sha256(script).hexdigest() != expected:
        raise SqlCtxError(
            "UPDATE_RELEASE_UNVERIFIED",
            f"{name} from {tag} does not match its SHA256SUMS; no installation was started.",
        )
    target = directory / name
    target.write_bytes(script)
    return target


def run_release_updater(
    *,
    host: str,
    version: str | None,
    fetch: Fetch = _fetch,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[int, str]:
    """Run the latest release's verified updater for one host; return its exit code and version."""
    tag = latest_release_tag(fetch)
    if version is not None and tag != f"v{version}":
        raise SqlCtxError(
            "UPDATE_SOURCE_REQUIRED",
            f"No install provenance exists, and without it only the latest release ({tag}) can "
            f"be installed. Pass `--source <release-checkout>` to install {version}.",
            details={"requested": version, "latest": tag.removeprefix("v")},
        )
    if sys.platform == "win32":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise SqlCtxError(
                "POWERSHELL_UNAVAILABLE", "PowerShell is required for product update on Windows."
            )
        prefix = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    else:
        shell = shutil.which("sh") or shutil.which("bash")
        if shell is None:
            raise SqlCtxError("SHELL_UNAVAILABLE", "A POSIX shell is required for product update.")
        prefix = [shell]
    with tempfile.TemporaryDirectory(prefix="sqlctx-release-update-") as temporary:
        script = fetch_verified_updater(tag, Path(temporary), fetch)
        environment = {**os.environ, "SQLCTX_HOST": host}
        environment.pop("SQLCTX_DRY_RUN", None)
        result = run(  # noqa: S603 - verified release script and closed shell arguments.
            [*prefix, str(script)], check=False, cwd=temporary, env=environment
        )
    return result.returncode, tag.removeprefix("v")
