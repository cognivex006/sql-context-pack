"""A pull through the real facade, catalog, SQLFluff, export and assembly; only the DB is fake."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from sqlctx.adapters.mysql import MySqlAdapter
from sqlctx.application.pull import PullRequest
from sqlctx.core.enums import DatabaseEngine
from sqlctx.exporting.header import parse_managed_sql
from sqlctx.exporting.validation import inventory_output
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.server import facade as facade_module
from sqlctx.server.facade import ServiceFacade
from tests.integration.test_catalog_service import (
    CatalogConnection,
    CatalogCursor,
    resolved_profile,
)


class SchemaCursor(CatalogCursor):
    def execute(self, query: str, parameters: object = ()) -> None:
        normalized = " ".join(query.lower().split())
        if "schemata" in normalized or "schema_name" in normalized and "tables" not in normalized:
            self.description, self.rows = [("schema_name",)], [("app",)]
            return
        super().execute(query, parameters)


class Connection(CatalogConnection):
    def cursor(self) -> SchemaCursor:
        return SchemaCursor()


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlfluff") is None, reason="SQLFluff not installed"
)


class Profiles:
    def resolve(self, name: str):  # type: ignore[no-untyped-def]
        assert name == "demo"
        return resolved_profile()


def test_one_pull_writes_validated_context_into_the_registered_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        facade_module, "create_adapter", lambda engine: MySqlAdapter(lambda _: Connection())
    )
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    manager = SqlFluffManager(state, python_executable=Path(sys.executable))
    facade = ServiceFacade(
        state=state,
        profiles=Profiles(),  # type: ignore[arg-type]
        overrides_path=tmp_path / "overrides.yaml",
        manager=manager,
    )
    if not manager.status().ready:
        pytest.skip("pinned SQLFluff is not installed in this interpreter")
    source = tmp_path / "sql-in"
    source.mkdir()
    output = tmp_path / "context-out"
    folder = facade.managed_folders.register(
        input_root=source, output_root=output, engine=DatabaseEngine.MYSQL
    )

    started = facade.pulls.start(
        PullRequest(profile="demo", folder_id=folder.folder_id), caller="agent:e2e"
    )
    assert started.status == "queued", started.blockers
    status = started
    for _ in range(240):
        status = facade.pulls.wait(started.pull_id, timeout_seconds=1)
        if status.status not in {"queued", "running"}:
            break
    assert status.status in {"completed", "completed_with_warnings"}, (
        status.error_code,
        status.error_message,
    )
    assert status.counts.discovered == 2
    assert status.counts.analyzed == 2
    assert status.counts.formatted + status.counts.parse_failed == status.counts.materialized
    assert status.counts.files_written > 0
    # Without an actor ID the index sync is skipped with a warning, never a failure.
    assert [item.code for item in status.warnings] == ["INDEX_ACTOR_ID_MISSING"]

    inventory = inventory_output(output)
    assert len(inventory.files) == status.counts.files_written
    managed_sql = sorted(output.rglob("*.sql"))
    assert len(managed_sql) == 2
    for path in managed_sql:
        header, _ = parse_managed_sql(path.read_text(encoding="utf-8"))
        assert header.schema_name == "app"
    assert "real-user-" not in "".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file()
    )

    # A second pull of the same scope replaces its own managed files and keeps nothing stale.
    again = facade.pulls.start(
        PullRequest(profile="demo", folder_id=folder.folder_id), caller="agent:e2e"
    )
    for _ in range(240):
        again = facade.pulls.wait(again.pull_id, timeout_seconds=1)
        if again.status not in {"queued", "running"}:
            break
    assert again.status in {"completed", "completed_with_warnings"}, again.error_code
    assert again.counts.stale_files_kept == 0
