"""Build the one-line installer assets for a release tag, and optionally upload them.

    python scripts/build_release_assets.py --tag v3.5.1 --output <empty-folder>
    python scripts/build_release_assets.py --tag v3.5.1 --upload

The bundle is ``git archive`` of the tag with canonical LF bytes, so it holds exactly what was
committed and tagged: a file that exists only in a working tree can never ship in it. The four
bootstrap scripts are read from ``--installer-ref`` (default ``HEAD``) for the same reason.
``--upload`` builds under the OS temporary directory, uploads with ``gh release upload
--clobber`` and removes the build folder afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "cognivex006/sql-context-pack"
BUNDLE = "sql-context-pack-install.zip"
BOOTSTRAPS = ("install.ps1", "update.ps1", "install.sh", "update.sh")


class AssetError(RuntimeError):
    pass


def _git(*arguments: str) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise AssetError("Git is required to build release assets.")
    result = subprocess.run(  # noqa: S603 - resolved Git executable and closed arguments.
        [git, "-C", str(ROOT), *arguments], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise AssetError(
            f"git {' '.join(arguments)} failed: {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def checksum_line(name: str, data: bytes) -> str:
    """The `sha256sum` text format the bootstraps parse: digest, two spaces, file name."""
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n"


def build(tag: str, output: Path, installer_ref: str = "HEAD") -> list[Path]:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise AssetError("--tag must look like v3.5.1.")
    if _git("cat-file", "-t", f"refs/tags/{tag}").strip() != b"tag":
        raise AssetError(f"{tag} is not an annotated tag.")
    manifest = json.loads(_git("show", f"{tag}:.codex-plugin/plugin.json"))
    if manifest.get("version") != tag[1:]:
        raise AssetError(f"{tag} declares product version {manifest.get('version')}.")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise AssetError(f"{output} is not empty.")
    bundle = output / BUNDLE
    # Without these, a Windows checkout with core.autocrlf=true would write CRLF into the
    # archive, and the bundled install.sh would no longer run under bash.
    _git(
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
        "archive",
        "--format=zip",
        f"--output={bundle}",
        f"refs/tags/{tag}",
    )
    written = [bundle]
    checksum = output / f"{BUNDLE}.sha256"
    checksum.write_bytes(checksum_line(BUNDLE, bundle.read_bytes()).encode("ascii"))
    written.append(checksum)
    for name in BOOTSTRAPS:
        data = _git("show", f"{installer_ref}:installer/{name}")
        if not data.isascii():
            raise AssetError(f"installer/{name} must be ASCII.")
        if name.endswith(".sh") and b"\r" in data:
            raise AssetError(f"installer/{name} must use LF line endings.")
        target = output / name
        target.write_bytes(data)
        written.append(target)
    sums = output / "SHA256SUMS"
    sums.write_bytes(
        "".join(checksum_line(path.name, path.read_bytes()) for path in written).encode("ascii")
    )
    written.append(sums)
    return written


def _gh(*arguments: str, capture: bool = False) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise AssetError("The GitHub CLI (gh) is required for --upload.")
    result = subprocess.run(  # noqa: S603 - resolved gh executable and closed arguments.
        [gh, *arguments, "--repo", REPOSITORY], capture_output=capture, text=True, check=False
    )
    if result.returncode != 0:
        raise AssetError(f"gh {' '.join(arguments)} failed.")
    return result.stdout if capture else ""


def upload(tag: str, files: list[Path]) -> dict[str, object]:
    _gh("release", "view", tag, "--json", "tagName", capture=True)
    latest = json.loads(_gh("release", "view", "--json", "tagName", capture=True))["tagName"]
    _gh("release", "upload", tag, *map(str, files), "--clobber")
    return {"uploaded": [path.name for path in files], "latest_release": latest}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--installer-ref", default="HEAD")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--output", type=Path)
    target.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    try:
        if args.output is not None:
            files = build(args.tag, args.output.resolve(), args.installer_ref)
            result: dict[str, object] = {"tag": args.tag, "files": [str(path) for path in files]}
        else:
            with tempfile.TemporaryDirectory(prefix="sqlctx-release-assets-") as temporary:
                files = build(args.tag, Path(temporary), args.installer_ref)
                result = {"tag": args.tag, **upload(args.tag, files)}
            if result["latest_release"] != args.tag:
                result["warning"] = (
                    f"{args.tag} is not the latest release; the one-line installers read "
                    f"assets from {result['latest_release']} only."
                )
    except AssetError as error:
        print(json.dumps({"ok": False, "message": str(error)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
