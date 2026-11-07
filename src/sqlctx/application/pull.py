"""One server-side pull job: discover, analyze, export, assemble, validate and index.

Requirement v1.9. The agent starts one job and waits on it. Every step reuses the service's
existing, reviewed operations; this module only orders them, owns their idempotency keys, and
reports one result. It writes nothing outside a registered folder's output root.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Condition, Lock, Thread
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sqlctx.core.enums import JobStatus, MaterializationMode, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import MaterializationSelection

if TYPE_CHECKING:
    from sqlctx.server.facade import ServiceFacade

WAIT_LIMIT_SECONDS = 50.0
INDEX_BATCH = 5_000
_TERMINAL = {"completed", "completed_with_warnings", "failed", "blocked", "cancelled"}
_EXPORT_TERMINAL = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_WARNINGS,
    JobStatus.PARTIAL,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}
PullState = Literal[
    "queued", "running", "completed", "completed_with_warnings", "failed", "blocked", "cancelled"
]


def _now() -> datetime:
    return datetime.now(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PullRequest(_Model):
    profile: str = Field(min_length=1)
    folder_id: str = Field(min_length=1)
    schemas: list[str] | None = None
    object_types: list[ObjectType] | None = None
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    sample_rows_per_table: int | None = Field(default=None, ge=10, le=20)
    actor_id: int | None = Field(default=None, gt=0)
    session_cache_key: str | None = Field(default=None, max_length=128)


class PullNotice(_Model):
    code: str
    message: str
    remedy: str | None = None


class PullCounts(_Model):
    discovered: int = 0
    analyzed: int = 0
    analysis_failed: int = 0
    materialized: int = 0
    formatted: int = 0
    parse_failed: int = 0
    format_failed: int = 0
    security_skipped: int = 0
    files_written: int = 0
    stale_files_kept: int = 0
    index_submitted: int = 0
    index_inserted: int = 0
    index_updated: int = 0
    index_unchanged: int = 0
    index_owner_values_preserved: int = 0


class PullStatus(_Model):
    pull_id: str
    status: PullState
    phase: str
    profile: str
    folder_id: str
    selection_mode: Literal["all", "filtered"]
    progress: int = 0
    current_object_id: str | None = None
    catalog_id: str | None = None
    export_id: str | None = None
    counts: PullCounts = Field(default_factory=PullCounts)
    blockers: list[PullNotice] = Field(default_factory=list)
    warnings: list[PullNotice] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    elapsed_seconds: float = 0


class PullService:
    """Runs pull jobs in background threads and keeps their status in runtime state."""

    def __init__(self, facade: ServiceFacade) -> None:
        self.facade = facade
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._running: dict[str, str] = {}
        self._recover_interrupted()

    def _recover_interrupted(self) -> None:
        """A pull whose service stopped mid-job would otherwise read `running` forever."""
        root = self.facade.state.root / "pulls"
        if not root.is_dir():
            return
        for path in root.glob("pull_*.json"):
            try:
                status = self._read(path.stem)
            except (SqlCtxError, ValueError):
                continue
            if status.status in {"queued", "running"}:
                status.status = "failed"
                status.phase = "failed"
                status.error_code = "PULL_INTERRUPTED"
                status.error_message = "The service restarted during this pull; start it again."
                status.completed_at = status.updated_at = _now()
                self._write(status)

    # ---------------------------------------------------------------- public operations
    def start(self, request: PullRequest, *, caller: str) -> PullStatus:
        fingerprint = self._fingerprint(request, caller)
        with self._lock:
            existing = self._running.get(fingerprint)
            if existing is not None:
                return self._read(existing)
        now = _now()
        status = PullStatus(
            pull_id="pull_" + secrets.token_urlsafe(16),
            status="queued",
            phase="preflight",
            profile=request.profile,
            folder_id=request.folder_id,
            selection_mode="filtered" if request.include_patterns else "all",
            created_at=now,
            updated_at=now,
        )
        blockers, warnings, plan = self.preflight(request)
        status.warnings = warnings
        if blockers:
            status.status = "blocked"
            status.phase = "blocked"
            status.blockers = blockers
            status.completed_at = _now()
            self._write(status)
            return status
        self._write(status)
        with self._lock:
            self._running[fingerprint] = status.pull_id
        Thread(
            target=self._run,
            args=(status.pull_id, request, plan, caller, fingerprint),
            name=f"sqlctx-{status.pull_id}",
            daemon=True,
        ).start()
        return status

    def status(self, pull_id: str) -> PullStatus:
        return self._read(pull_id)

    def wait(self, pull_id: str, *, timeout_seconds: float = WAIT_LIMIT_SECONDS) -> PullStatus:
        """Return when phase, progress or status changes, or after at most 50 seconds."""
        timeout = max(0.0, min(timeout_seconds, WAIT_LIMIT_SECONDS))
        first = self._read(pull_id)
        if first.status in _TERMINAL:
            return first
        marker = (first.status, first.phase, first.progress)
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                remaining = deadline - time.monotonic()
                current = self._read(pull_id)
                if (current.status, current.phase, current.progress) != marker:
                    return current
                if current.status in _TERMINAL or remaining <= 0:
                    return current
                self._changed.wait(timeout=min(remaining, 2.0))

    # ---------------------------------------------------------------- preflight
    def preflight(
        self, request: PullRequest
    ) -> tuple[list[PullNotice], list[PullNotice], dict[str, Any]]:
        """Check everything that would stop the job, and report all of it at once."""
        blockers: list[PullNotice] = []
        warnings: list[PullNotice] = []
        plan: dict[str, Any] = {}
        profile = None
        try:
            profile = self.facade.profiles.resolve(request.profile)
        except SqlCtxError as exc:
            blockers.append(
                PullNotice(
                    code=exc.code,
                    message="The profile cannot be used.",
                    remedy="sqlctx profile list",
                )
            )
        if profile is not None:
            schemas = request.schemas or list(profile.allowed_schemas)
            types = request.object_types or list(profile.allowed_object_types)
            outside_schemas = sorted(set(schemas) - set(profile.allowed_schemas))
            outside_types = sorted(
                str(item) for item in set(types) - set(profile.allowed_object_types)
            )
            if outside_schemas:
                blockers.append(
                    PullNotice(
                        code="SCHEMA_NOT_ALLOWED",
                        message=f"Schemas outside the profile allowlist: {outside_schemas}.",
                        remedy=f"sqlctx profile scope {request.profile} --schema <schema>",
                    )
                )
            if outside_types:
                blockers.append(
                    PullNotice(
                        code="OBJECT_TYPE_NOT_ALLOWED",
                        message=f"Object types outside the profile allowlist: {outside_types}.",
                        remedy=f"sqlctx profile scope {request.profile} --schema <schema> --object-type <type>",
                    )
                )
            plan.update(schemas=schemas, object_types=types, profile=profile)
            try:
                self.facade.test_profile(request.profile)
            except SqlCtxError as exc:
                blockers.append(
                    PullNotice(
                        code=exc.code,
                        message="The database did not answer a bounded read-only connection test.",
                        remedy=f"sqlctx profile test {request.profile}",
                    )
                )
            if request.actor_id is None:
                warnings.append(
                    PullNotice(
                        code="INDEX_ACTOR_ID_MISSING",
                        message="No actor ID was given, so the context index sync is skipped.",
                        remedy="Pass actor_id (the owner's numeric ID) to include the index sync.",
                    )
                )
            elif not getattr(profile, "metadata_context_write", False):
                warnings.append(
                    PullNotice(
                        code="METADATA_CONTEXT_WRITE_SCOPE_REQUIRED",
                        message="The profile does not allow context index writes; sync is skipped.",
                        remedy=f"sqlctx profile write-scope --profile {request.profile} --metadata-context-write",
                    )
                )
            elif str(profile.engine) != "sqlserver":
                warnings.append(
                    PullNotice(
                        code="METADATA_CONTEXT_ENGINE_UNSUPPORTED",
                        message="The context index is SQL Server only; files are still written.",
                    )
                )
        try:
            folder = self.facade.managed_folders._folder(request.folder_id)
            plan["output_root"] = Path(folder.output_root)
        except SqlCtxError as exc:
            blockers.append(
                PullNotice(
                    code=exc.code,
                    message="The folder ID is not registered.",
                    remedy="sqlctx folder register --input-root <input> --output-root <output> --engine <engine>",
                )
            )
        try:
            tooling = self.facade.manager.status()
            if not tooling.ready:
                raise SqlCtxError("TOOLING_UNAVAILABLE", "SQLFluff is not ready.")
        except SqlCtxError as exc:
            blockers.append(
                PullNotice(
                    code=exc.code if exc.code else "TOOLING_UNAVAILABLE",
                    message="Pinned SQLFluff is not ready on the service host.",
                    remedy="sqlctx sqlfluff ensure",
                )
            )
        return blockers, warnings, plan

    # ---------------------------------------------------------------- job
    def _run(
        self,
        pull_id: str,
        request: PullRequest,
        plan: dict[str, Any],
        caller: str,
        fingerprint: str,
    ) -> None:
        started = time.monotonic()
        try:
            self._catalog_phase(pull_id, request, plan, caller)
            self._export_phase(pull_id, caller)
            self._assemble_phase(pull_id, plan["output_root"])
            self._index_phase(pull_id, request, plan["output_root"], caller)
            self._update(pull_id, phase="completed", finish=True, started=started)
        except SqlCtxError as exc:
            self._update(
                pull_id,
                status="failed",
                phase="failed",
                error_code=exc.code,
                error_message=exc.message,
                finish=True,
                started=started,
            )
        except Exception:  # noqa: BLE001 - never leak internals; the job must reach a state.
            self._update(
                pull_id,
                status="failed",
                phase="failed",
                error_code="PULL_INTERNAL_ERROR",
                error_message="The pull job stopped unexpectedly; see the service log.",
                finish=True,
                started=started,
            )
        finally:
            with self._lock:
                self._running.pop(fingerprint, None)
                self._changed.notify_all()

    def _catalog_phase(
        self, pull_id: str, request: PullRequest, plan: dict[str, Any], caller: str
    ) -> None:
        from sqlctx.server.contracts import CreateCatalogRequest, SamplePolicy

        self._update(pull_id, status="running", phase="analyzing")
        mode = MaterializationMode.FILTERED if request.include_patterns else MaterializationMode.ALL
        sample = SamplePolicy(rows_per_table=request.sample_rows_per_table)
        command = CreateCatalogRequest(
            profile=request.profile,
            schemas=plan["schemas"],
            object_types=plan["object_types"],
            include_patterns=request.include_patterns,
            exclude_patterns=request.exclude_patterns,
            selection=MaterializationSelection(mode=mode),
            sample=sample,
            session_cache_key=request.session_cache_key,
        )
        status, _ = self.facade.create_catalog(
            command, caller=caller, idempotency_key=f"{pull_id}-catalog"
        )
        self._update(
            pull_id,
            catalog_id=status.catalog_id,
            counts={
                "discovered": status.discovered_object_count,
                "analyzed": status.fully_analyzed_object_count,
                "analysis_failed": status.analysis_failed_object_count,
                "materialized": status.materialized_object_count,
            },
        )
        if status.analysis_failed_object_count:
            self._warn(
                pull_id,
                "ANALYSIS_FAILURES",
                f"{status.analysis_failed_object_count} objects could not be analyzed; "
                "the rest were exported.",
                f"sqlctx_get_catalog_status catalog_id={status.catalog_id}",
            )

    def _export_phase(self, pull_id: str, caller: str) -> None:
        from sqlctx.server.contracts import ExportCreateRequest

        catalog_id = self._read(pull_id).catalog_id
        assert catalog_id is not None
        self._update(pull_id, phase="formatting", progress=0)
        export, _ = self.facade.create_export(
            ExportCreateRequest(catalog_id=catalog_id),
            caller=caller,
            idempotency_key=f"{pull_id}-export",
        )
        self._update(pull_id, export_id=export.export_id)
        while export.status not in _EXPORT_TERMINAL:
            time.sleep(1.0)
            export = self.facade.exports.status(export.export_id)
            self._update(
                pull_id,
                progress=export.processed_object_count,
                current_object_id=export.current_object_id,
            )
        if export.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise SqlCtxError(
                export.error_code or "EXPORT_FAILED", "The export did not produce a bundle."
            )
        objects = export.objects
        self._update(
            pull_id,
            current_object_id=None,
            counts={
                "formatted": objects.succeeded - objects.parse_failed - objects.failed,
                "parse_failed": objects.parse_failed,
                "format_failed": objects.failed,
                "security_skipped": objects.skipped_security,
            },
        )

    def _assemble_phase(self, pull_id: str, output_root: Path) -> None:
        from sqlctx.exporting.assembly import assemble_bundles
        from sqlctx.exporting.validation import inventory_output, validate_bundle

        self._update(pull_id, phase="writing")
        export_id = self._read(pull_id).export_id
        assert export_id is not None
        export = self.facade.exports.status(export_id)
        bundle = self.facade.exports.artifact_path(export_id)
        validate_bundle(
            bundle, expected_size=int(export.size_bytes or 0), expected_sha256=str(export.sha256)
        )
        kept: list[str] = []
        assembled = assemble_bundles([bundle], output_root, keep_stale=True, kept_stale=kept)
        reread = inventory_output(output_root)
        if reread.inventory_sha256 != assembled.inventory_sha256:
            raise SqlCtxError("OUTPUT_VALIDATION_FAILED", "Written files did not reread cleanly.")
        self._update(
            pull_id,
            counts={"files_written": len(reread.files), "stale_files_kept": len(kept)},
        )
        if kept:
            self._warn(
                pull_id,
                "STALE_MANAGED_FILES_KEPT",
                f"{len(kept)} files from an earlier pull no longer match a database object and "
                "were kept.",
                "After review: sqlctx export fetch, then sqlctx export assemble --allow-delete-stale.",
            )

    def _index_phase(
        self, pull_id: str, request: PullRequest, output_root: Path, caller: str
    ) -> None:
        from sqlctx.context_index.contracts import ContextIndexEntry, ContextIndexSyncRequest
        from sqlctx.exporting.header import parse_managed_sql

        current = self._read(pull_id)
        if any(
            notice.code
            in {
                "INDEX_ACTOR_ID_MISSING",
                "METADATA_CONTEXT_WRITE_SCOPE_REQUIRED",
                "METADATA_CONTEXT_ENGINE_UNSUPPORTED",
            }
            for notice in current.warnings
        ):
            return
        assert request.actor_id is not None
        self._update(pull_id, phase="indexing")
        entries: list[ContextIndexEntry] = []
        preserved = 0
        for path in sorted(output_root.rglob("*.sql")):
            relative = PurePosixPath(path.relative_to(output_root).as_posix())
            try:
                header, _ = parse_managed_sql(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if header.classification_source == "owner":
                # Inventory mode never carries owner classifications; they stay as recorded.
                preserved += 1
                continue
            entries.append(
                ContextIndexEntry(
                    schema_name=header.schema_name,
                    object_name=header.object_name,
                    object_type=ObjectType(str(header.object_type)),
                    context=header.context,
                    description=header.description,
                    tags=list(header.tags),
                    classification_status=header.classification_status,
                    classification_source=header.classification_source,
                    source_fingerprint=header.source_fingerprint,
                    content_hash=header.content_hash,
                    managed_relative_path=str(relative),
                    evidence=list(header.evidence),
                )
            )
        totals = {"inserted": 0, "updated": 0, "unchanged": 0, "owner_values_preserved": preserved}
        for start in range(0, len(entries), INDEX_BATCH):
            batch = entries[start : start + INDEX_BATCH]
            result = self.facade.sync_context_index(
                ContextIndexSyncRequest(
                    profile=request.profile,
                    entries=batch,
                    actor_id=request.actor_id,
                    idempotency_key=f"{pull_id}-index-{start // INDEX_BATCH}",
                    mode="inventory",
                ),
                caller=caller,
            )
            values = result.model_dump(mode="json")
            for key in ("inserted", "updated", "unchanged", "owner_values_preserved"):
                totals[key] += int(values.get(key, 0) or 0)
        self._update(
            pull_id,
            counts={
                "index_submitted": len(entries),
                "index_inserted": totals["inserted"],
                "index_updated": totals["updated"],
                "index_unchanged": totals["unchanged"],
                "index_owner_values_preserved": totals["owner_values_preserved"],
            },
        )

    # ---------------------------------------------------------------- state
    @staticmethod
    def _fingerprint(request: PullRequest, caller: str) -> str:
        payload = json.dumps(
            {"caller": caller, **request.model_dump(mode="json", exclude={"session_cache_key"})},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _read(self, pull_id: str) -> PullStatus:
        if not pull_id.startswith("pull_") or any(char in pull_id for char in "/\\."):
            raise SqlCtxError("PULL_NOT_FOUND", "Pull job was not found.", status_code=404)
        raw = self.facade.state.read_json(f"pulls/{pull_id}.json")
        if not isinstance(raw, dict):
            raise SqlCtxError("PULL_NOT_FOUND", "Pull job was not found.", status_code=404)
        return PullStatus.model_validate(raw)

    def _write(self, status: PullStatus) -> None:
        self.facade.state.write_json(f"pulls/{status.pull_id}.json", status.model_dump(mode="json"))

    def _warn(self, pull_id: str, code: str, message: str, remedy: str | None = None) -> None:
        def add(status: PullStatus) -> None:
            status.warnings.append(PullNotice(code=code, message=message, remedy=remedy))

        self._mutate(pull_id, add)

    def _update(
        self,
        pull_id: str,
        *,
        finish: bool = False,
        started: float | None = None,
        counts: dict[str, int] | None = None,
        **fields: Any,
    ) -> None:
        def apply(status: PullStatus) -> None:
            for key, value in fields.items():
                setattr(status, key, value)
            if counts:
                status.counts = status.counts.model_copy(update=counts)
            if finish:
                if status.status not in {"failed", "cancelled"}:
                    status.status = "completed_with_warnings" if status.warnings else "completed"
                status.completed_at = _now()
                if started is not None:
                    status.elapsed_seconds = round(time.monotonic() - started, 3)

        self._mutate(pull_id, apply)

    def _mutate(self, pull_id: str, change: Callable[[PullStatus], None]) -> None:
        with self._lock:
            status = self._read(pull_id)
            change(status)
            status.updated_at = _now()
            self._write(status)
            self._changed.notify_all()
