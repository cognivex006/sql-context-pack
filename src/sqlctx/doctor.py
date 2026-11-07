"""Read-only version/readiness inspection; installation belongs to explicit CLI update."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from sqlctx._version import __version__
from sqlctx.security.key_protection import is_wrapped, wrapping_supported
from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.service_diagnosis import build_finding, diagnose_service

REPOSITORY = "cognivex006/sql-context-pack"


# Antigravity discovers skills and MCP servers by file placement; it exposes no plugin or
# extension listing, so its installed version comes from the staged manifest instead.
_FILE_BASED_HOSTS = {"agy"}
_HOSTS = {"codex", "claude", "gemini", "agy"}


def _native_plugin_version(host: str) -> str | None:
    if host in _FILE_BASED_HOSTS:
        return None
    executable = shutil.which(host)
    if executable is None:
        return None
    arguments = (
        [executable, "plugin", "list", "--json"]
        if host != "gemini"
        else [executable, "extensions", "list"]
    )
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, check=False, timeout=15)  # noqa: S603 - closed host CLI and read-only list arguments.
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
        items = payload.get("installed", []) if isinstance(payload, dict) else payload
        matches = [
            item
            for item in items
            if isinstance(item, dict)
            and (
                item.get("name") == "sql-context-pack"
                or any(
                    str(item.get(key, "")).startswith("sql-context-pack@")
                    for key in ("pluginId", "id")
                )
            )
            and item.get("enabled", True)
        ]
        if len(matches) == 1:
            # Claude Code lists plugins under `id`. A plugin loaded from a local marketplace
            # folder reports the version it actually loads as `folderVersion`; `version` is
            # the cache entry recorded at the last install and can be older.
            match = matches[0]
            version = (
                match.get("folderVersion") if match.get("readFromFolder") else None
            ) or match.get("version")
            return version if isinstance(version, str) else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    return None


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _skill_md_version(path: Path) -> str | None:
    """Read metadata.version from SKILL.md frontmatter without a YAML dependency."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    frontmatter = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    match = re.search(r"^\s*version:\s*[\"']?(\d+\.\d+\.\d+)[\"']?\s*$", frontmatter, re.MULTILINE)
    return match.group(1) if match else None


def source_version(source: Path) -> str | None:
    value = _json(source / ".codex-plugin/plugin.json").get("version")
    return value if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value) else None


_MASTER_KEYS = ("profile-credentials/master.key", "master.key")


def _credential_key_findings(runtime_root: Path) -> list[dict[str, Any]]:
    """Read-only: report master keys still stored as plain files on a DPAPI-capable host."""
    if not wrapping_supported():
        return []
    unwrapped = 0
    for relative in _MASTER_KEYS:
        try:
            stored = (runtime_root / relative).read_bytes()
        except OSError:
            continue
        unwrapped += not is_wrapped(stored)
    if not unwrapped:
        return []
    return [
        {
            "code": "CREDENTIAL_KEY_UNWRAPPED",
            "evidence": {"unwrapped_key_count": unwrapped},
            "action": (
                "No owner step is needed: the service protects each key with machine-scope "
                "DPAPI the next time it opens it (profile connect or export). Never delete or "
                "recreate a master key; existing credentials depend on it."
            ),
        }
    ]


def inspect_installation(
    *, source: Path | None = None, host: str = "codex", online: bool = False
) -> dict[str, Any]:
    """Inspect only safe metadata. No facade construction, writes or database connections."""
    if host not in _HOSTS:
        raise ValueError("Unsupported host")
    root = Path.home()
    installed = root / {
        "gemini": ".gemini/extensions/sql-context-pack",
        "agy": ".gemini/config/skills/sql-context-pack",
    }.get(host, "plugins/sql-context-pack")
    manifest = {
        "codex": ".codex-plugin/plugin.json",
        "claude": ".claude-plugin/plugin.json",
        "gemini": "gemini-extension.json",
        "agy": ".agents/plugin.json",
    }[host]
    staged_plugin_version: object = _json(installed / manifest).get("version")
    if host in _FILE_BASED_HOSTS and not isinstance(staged_plugin_version, str):
        # Antigravity installs the skill layout, which has no manifest; the installed
        # SKILL.md frontmatter is the authoritative version there.
        staged_plugin_version = _skill_md_version(installed / "SKILL.md")
    plugin_version = _native_plugin_version(host)
    if host in _FILE_BASED_HOSTS:
        # There is no native listing to cross-check, so the staged version is the
        # installed version rather than an unverified hint.
        plugin_version = staged_plugin_version if isinstance(staged_plugin_version, str) else None
    provenance = _json(installed / ".sqlctx-install.json")
    if source is None and isinstance(provenance.get("source_root"), str):
        source = Path(provenance["source_root"])
    available = source_version(source) if source is not None else None
    findings: list[dict[str, Any]] = []
    release_version = None
    if online:
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                response = client.get(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
                response.raise_for_status()
                tag = response.json().get("tag_name", "")
                if re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
                    release_version = tag.removeprefix("v")
        except (httpx.HTTPError, ValueError, AttributeError):
            findings.append(
                {
                    "code": "RELEASE_CHECK_UNAVAILABLE",
                    "action": "Retry doctor check --online; no installation was changed.",
                }
            )
    state = JsonRuntimeStateStore()
    service_version = None
    try:
        metadata = state.read_json("connection-metadata.json")
    except Exception:  # noqa: BLE001 - never include metadata contents or paths.
        findings.append(build_finding("SERVICE_METADATA_INVALID", host=host, evidence={}))
    else:
        if metadata is None:
            findings.append(
                {
                    "code": "SERVICE_NOT_CONFIGURED",
                    "action": "Install or start the managed service.",
                }
            )
        else:
            service_findings, service_version = diagnose_service(metadata, host=host)
            findings.extend(service_findings)
    findings.extend(_credential_key_findings(state.root))
    versions = {
        "package": __version__,
        "plugin": plugin_version,
        "staged_plugin": staged_plugin_version,
        "service": service_version,
        "source": available,
        "release": release_version,
    }
    for surface in ("plugin", "service"):
        value = versions[surface]
        if value is not None and value != __version__:
            findings.append(
                {"code": "VERSION_DRIFT", "action": f"Update or repair {surface} to {__version__}."}
            )
    if plugin_version is None and host not in _FILE_BASED_HOSTS:
        findings.append(
            {
                "code": "PLUGIN_VERSION_UNVERIFIED",
                "action": "Check the selected host's native plugin listing; cache-only installs may not have local staging metadata.",
            }
        )
    candidates = [v for v in (available, release_version) if isinstance(v, str)]
    update_available = any(
        tuple(map(int, v.split("."))) > tuple(map(int, __version__.split("."))) for v in candidates
    )
    return {
        "status": "attention" if findings or update_available else "ready",
        "host": host,
        "platform": platform.system(),
        "python": ".".join(map(str, sys.version_info[:3])),
        "versions": versions,
        "update_available": update_available,
        "findings": findings,
        "update_command": f"sqlctx doctor update --host {host}",
    }
