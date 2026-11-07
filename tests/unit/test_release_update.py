"""doctor update without install provenance uses the release's verified updater."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import typer

from sqlctx import release_update
from sqlctx.cli import main
from sqlctx.core.errors import SqlCtxError

SCRIPT = b"# release updater\n"


def _fetcher(tag: str = "v3.5.7", script: bytes = SCRIPT, digest: str | None = None) -> Any:
    name = release_update.updater_name()
    listed = digest or hashlib.sha256(script).hexdigest()

    def fetch(url: str) -> bytes:
        if url.endswith("/releases/latest"):
            return json.dumps({"tag_name": tag}).encode()
        if url.endswith("/SHA256SUMS"):
            return f"{'0' * 64}  sql-context-pack-install.zip\n{listed}  {name}\n".encode()
        if url.endswith(f"/{name}"):
            return script
        raise AssertionError(url)

    return fetch


def test_the_latest_updater_runs_for_one_host(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"command": command, **kwargs})
        assert Path(command[-1]).read_bytes() == SCRIPT
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("SQLCTX_DRY_RUN", "1")
    code, version = release_update.run_release_updater(
        host="claude", version=None, fetch=_fetcher(), run=run
    )
    assert (code, version) == (0, "3.5.7")
    assert calls[0]["env"]["SQLCTX_HOST"] == "claude"
    assert "SQLCTX_DRY_RUN" not in calls[0]["env"]


def test_the_latest_version_may_be_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0)

    assert release_update.run_release_updater(
        host="claude", version="3.5.7", fetch=_fetcher(), run=run
    ) == (0, "3.5.7")


def test_a_pinned_older_version_is_refused_not_replaced_by_latest() -> None:
    def run(*_: Any, **__: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("nothing may run")

    with pytest.raises(SqlCtxError) as error:
        release_update.run_release_updater(
            host="claude", version="3.5.4", fetch=_fetcher(), run=run
        )
    assert error.value.code == "UPDATE_SOURCE_REQUIRED"
    assert error.value.details == {"requested": "3.5.4", "latest": "3.5.7"}


def test_an_updater_that_does_not_match_its_checksum_never_runs() -> None:
    def run(*_: Any, **__: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("nothing may run")

    with pytest.raises(SqlCtxError) as error:
        release_update.run_release_updater(
            host="claude", version=None, fetch=_fetcher(digest="f" * 64), run=run
        )
    assert error.value.code == "UPDATE_RELEASE_UNVERIFIED"


def test_doctor_update_falls_back_only_when_provenance_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> Path:
        raise SqlCtxError("UPDATE_SOURCE_REQUIRED", "no provenance")

    verified: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "_installed_source_root", missing)
    monkeypatch.setattr(release_update, "run_release_updater", lambda **kwargs: (0, "3.5.7"))
    monkeypatch.setattr(main, "_verify_updated_versions", lambda h, v: verified.append((h, v)))
    with pytest.raises(typer.Exit) as exit_info:
        main.product_update(source=None, host="claude", version=None)
    assert exit_info.value.exit_code == 0
    assert verified == [("claude", "3.5.7")]


def test_unreadable_provenance_still_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable() -> Path:
        raise SqlCtxError("UPDATE_PROVENANCE_INVALID", "unreadable")

    monkeypatch.setattr(main, "_installed_source_root", unreadable)
    with pytest.raises(SqlCtxError) as error:
        main.product_update(source=None, host="claude", version=None)
    assert error.value.code == "UPDATE_PROVENANCE_INVALID"
