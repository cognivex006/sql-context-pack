"""Requirement v1.9: one pull job, one preflight, one report."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sqlctx.application import pull as pull_module
from sqlctx.application.pull import PullRequest, PullService, PullStatus
from sqlctx.core.enums import DatabaseEngine, JobStatus, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.exporting.header import ManagedSqlHeader, render_managed_sql
from sqlctx.security.runtime import JsonRuntimeStateStore


class FakeProfiles:
    def __init__(self, *, write: bool = True, engine: str = "sqlserver") -> None:
        self.profile = SimpleNamespace(
            allowed_schemas=("app",),
            allowed_object_types=(ObjectType.TABLE, ObjectType.PROCEDURE),
            metadata_context_write=write,
            engine=engine,
        )

    def resolve(self, name: str) -> Any:
        if name != "dev":
            raise SqlCtxError("PROFILE_NOT_FOUND", "missing")
        return self.profile


class FakeFacade:
    def __init__(self, tmp_path: Path, *, write: bool = True, export_fails: bool = False) -> None:
        self.state = JsonRuntimeStateStore(tmp_path / "runtime")
        self.profiles = FakeProfiles(write=write)
        self.output_root = tmp_path / "out"
        self.output_root.mkdir()
        self.export_fails = export_fails
        self.synced: list[Any] = []
        self.release = threading.Event()
        self.release.set()
        folder = SimpleNamespace(output_root=str(self.output_root))
        self.managed_folders = SimpleNamespace(
            _folder=lambda folder_id: (
                folder
                if folder_id == "fld_ok"
                else (_ for _ in ()).throw(SqlCtxError("FOLDER_NOT_FOUND", "missing"))
            )
        )
        self.manager = SimpleNamespace(status=lambda: SimpleNamespace(ready=True))
        self.exports = SimpleNamespace(
            status=self._export_status, artifact_path=lambda _: Path("b")
        )

    def test_profile(self, name: str) -> dict[str, Any]:
        return {"reachable": True}

    def create_catalog(self, command: Any, *, caller: str, idempotency_key: str) -> Any:
        self.release.wait(5)
        self.catalog_command = command
        return (
            SimpleNamespace(
                catalog_id="cat_1",
                discovered_object_count=3,
                fully_analyzed_object_count=3,
                analysis_failed_object_count=0,
                materialized_object_count=3,
            ),
            False,
        )

    def create_export(self, command: Any, *, caller: str, idempotency_key: str) -> Any:
        return self._export_status("exp_1"), False

    def _export_status(self, export_id: str) -> Any:
        return SimpleNamespace(
            export_id="exp_1",
            status=JobStatus.FAILED if self.export_fails else JobStatus.COMPLETED,
            error_code="EXPORT_FAILED" if self.export_fails else None,
            processed_object_count=3,
            current_object_id=None,
            size_bytes=1,
            sha256="sha256:x",
            objects=SimpleNamespace(succeeded=3, parse_failed=1, failed=0, skipped_security=0),
        )

    def sync_context_index(self, request: Any, *, caller: str) -> Any:
        self.synced.append(request)
        return SimpleNamespace(
            model_dump=lambda **_: {"inserted": len(request.entries), "updated": 0, "unchanged": 0}
        )


def _write_managed(root: Path, name: str, source: str) -> None:
    header = ManagedSqlHeader(
        object_id=f"table:app.{name}",
        engine=DatabaseEngine.SQLSERVER,
        schema_name="app",
        object_name=name,
        object_type=ObjectType.TABLE,
        context="app" if source != "unknown" else None,
        tags=["app", "table"] if source != "unknown" else [],
        classification_status="unresolved" if source == "unknown" else "confirmed",
        classification_source=source,  # type: ignore[arg-type]
        content_hash="sha256:" + "0" * 64,
        output_format_version="2",
    )
    target = root / "app" / "tables" / f"{name}.sql"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_managed_sql(header, "CREATE TABLE x (id int);\n"), encoding="utf-8")


@pytest.fixture(autouse=True)
def _fake_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlctx.exporting import assembly, validation

    inventory = SimpleNamespace(files=[1, 2, 3], inventory_sha256="sha256:i")
    monkeypatch.setattr(validation, "validate_bundle", lambda *a, **k: None)
    monkeypatch.setattr(validation, "inventory_output", lambda root: inventory)

    def assemble(bundles: list[Path], root: Path, **kwargs: Any) -> Any:
        kwargs["kept_stale"].append("app/tables/OLD.sql")
        return inventory

    monkeypatch.setattr(assembly, "assemble_bundles", assemble)


def _finish(service: PullService, status: PullStatus) -> PullStatus:
    for _ in range(100):
        status = service.wait(status.pull_id, timeout_seconds=1)
        if status.status not in {"queued", "running"}:
            return status
    raise AssertionError("pull did not finish")


def test_preflight_returns_every_blocker_at_once_and_starts_nothing(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path)
    facade.manager = SimpleNamespace(status=lambda: SimpleNamespace(ready=False))
    service = PullService(facade)  # type: ignore[arg-type]
    status = service.start(
        PullRequest(profile="dev", folder_id="fld_missing", schemas=["app", "secret"]),
        caller="agent",
    )
    assert status.status == "blocked"
    assert {item.code for item in status.blockers} == {
        "SCHEMA_NOT_ALLOWED",
        "FOLDER_NOT_FOUND",
        "TOOLING_UNAVAILABLE",
    }
    assert all(item.remedy for item in status.blockers)
    assert not hasattr(facade, "catalog_command")
    assert service.status(status.pull_id).status == "blocked"


def test_a_pull_runs_every_phase_and_reports_one_result(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path)
    for name, source in (("A", "rule"), ("B", "unknown"), ("C", "owner")):
        _write_managed(facade.output_root, name, source)
    service = PullService(facade)  # type: ignore[arg-type]
    status = _finish(
        service,
        service.start(PullRequest(profile="dev", folder_id="fld_ok", actor_id=6006), caller="a"),
    )
    assert status.status == "completed_with_warnings"
    assert [item.code for item in status.warnings] == ["STALE_MANAGED_FILES_KEPT"]
    assert facade.catalog_command.selection.mode == "all"
    assert facade.catalog_command.schemas == ["app"]
    counts = status.counts
    assert (counts.discovered, counts.analyzed, counts.materialized) == (3, 3, 3)
    assert (counts.formatted, counts.parse_failed, counts.files_written) == (2, 1, 3)
    assert counts.stale_files_kept == 1
    # The owner-confirmed file is never sent in inventory mode; it is reported as preserved.
    assert counts.index_submitted == 2 and counts.index_owner_values_preserved == 1
    assert facade.synced[0].mode == "inventory"
    assert facade.synced[0].idempotency_key == f"{status.pull_id}-index-0"


def test_include_patterns_use_filtered_mode(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path)
    service = PullService(facade)  # type: ignore[arg-type]
    _finish(
        service,
        service.start(
            PullRequest(profile="dev", folder_id="fld_ok", include_patterns=["LM_*"]), caller="a"
        ),
    )
    assert facade.catalog_command.selection.mode == "filtered"


def test_missing_actor_or_write_scope_skips_only_the_index(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path, write=False)
    service = PullService(facade)  # type: ignore[arg-type]
    no_actor = _finish(
        service, service.start(PullRequest(profile="dev", folder_id="fld_ok"), caller="a")
    )
    assert "INDEX_ACTOR_ID_MISSING" in [item.code for item in no_actor.warnings]
    no_scope = _finish(
        service,
        service.start(PullRequest(profile="dev", folder_id="fld_ok", actor_id=1), caller="b"),
    )
    assert "METADATA_CONTEXT_WRITE_SCOPE_REQUIRED" in [item.code for item in no_scope.warnings]
    assert facade.synced == []
    assert no_scope.counts.files_written == 3


def test_a_failed_export_ends_the_job_with_its_code(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path, export_fails=True)
    service = PullService(facade)  # type: ignore[arg-type]
    status = _finish(
        service, service.start(PullRequest(profile="dev", folder_id="fld_ok"), caller="a")
    )
    assert status.status == "failed"
    assert status.error_code == "EXPORT_FAILED"


def test_an_identical_running_pull_is_returned_not_started_twice(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path)
    facade.release.clear()
    service = PullService(facade)  # type: ignore[arg-type]
    request = PullRequest(profile="dev", folder_id="fld_ok")
    first = service.start(request, caller="a")
    second = service.start(request, caller="a")
    assert second.pull_id == first.pull_id
    facade.release.set()
    _finish(service, first)


def test_wait_is_bounded_and_returns_early_on_change(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path)
    facade.release.clear()
    service = PullService(facade)  # type: ignore[arg-type]
    status = service.start(PullRequest(profile="dev", folder_id="fld_ok"), caller="a")
    started = time.monotonic()
    waited = service.wait(status.pull_id, timeout_seconds=0.3)
    assert time.monotonic() - started < 2.5
    assert waited.pull_id == status.pull_id
    facade.release.set()
    assert _finish(service, status).status in {"completed", "completed_with_warnings"}
    assert pull_module.WAIT_LIMIT_SECONDS == 50.0


def test_unknown_or_unsafe_pull_ids_are_not_found(tmp_path: Path) -> None:
    service = PullService(FakeFacade(tmp_path))  # type: ignore[arg-type]
    for pull_id in ("pull_missing", "../etc", "pull_..\\x"):
        with pytest.raises(SqlCtxError) as error:
            service.status(pull_id)
        assert error.value.code == "PULL_NOT_FOUND"


def test_a_pull_left_running_by_a_restart_is_failed_on_startup(tmp_path: Path) -> None:
    facade = FakeFacade(tmp_path)
    now = pull_module._now()
    # What a service that stopped mid-job leaves behind: a record with no thread behind it.
    leftover = PullStatus(
        pull_id="pull_leftover",
        status="running",
        phase="formatting",
        profile="dev",
        folder_id="fld_ok",
        selection_mode="all",
        created_at=now,
        updated_at=now,
    )
    facade.state.write_json("pulls/pull_leftover.json", leftover.model_dump(mode="json"))
    recovered = PullService(facade).status("pull_leftover")  # type: ignore[arg-type]
    assert recovered.status == "failed"
    assert recovered.error_code == "PULL_INTERRUPTED"
    assert recovered.completed_at is not None
