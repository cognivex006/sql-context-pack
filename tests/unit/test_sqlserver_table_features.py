"""Regression: table feature probes on non-Latin1 databases and older servers (3.3.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sqlctx.adapters.base import database_error_diagnostic
from sqlctx.adapters.sqlserver import SqlServerAdapter, tables
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile

PROFILE = ResolvedConnectionProfile(
    name="demo",
    engine=DatabaseEngine.SQLSERVER,
    host="localhost",
    port=1433,
    database="demo",
    username="user",
    password="password",
    allowed_schemas=("agrimap_app",),
    allowed_object_types=(ObjectType.TABLE,),
)
REF = ObjectRef(
    object_id="table:agrimap_app.AUTH_FLOW",
    engine=DatabaseEngine.SQLSERVER,
    schema_name="agrimap_app",
    object_name="AUTH_FLOW",
    object_type=ObjectType.TABLE,
)

# The exact pyodbc error SQL Server 2025 raised for FEATURES_QUERY on a Thai_CI_AS database.
COLLATION_ERROR = (
    "42000",
    "[42000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Cannot resolve collation "
    'conflict between "Latin1_General_CI_AS_KS_WS" and "Thai_CI_AS" in concat operator '
    "occurring in ORDER BY statement column 2. (451) (SQLExecDirectW); [42000] [Microsoft]"
    "[ODBC Driver 17 for SQL Server][SQL Server]Statement(s) could not be prepared. (8180)",
)


class ProgrammingError(Exception):
    """Stands in for pyodbc.ProgrammingError."""


class ScriptedCursor:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.description: list[tuple[str]] = []
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str, parameters: Any = ()) -> None:
        normalized = " ".join(query.lower().split())
        self.description, self.rows = [], []
        for marker, outcome in self.script.items():
            if marker in normalized:
                if isinstance(outcome, BaseException):
                    raise outcome
                fields, rows = outcome
                self.description = [(name,) for name in fields]
                self.rows = list(rows)
                return

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        return None


class ScriptedConnection:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script

    def cursor(self) -> ScriptedCursor:
        return ScriptedCursor(self.script)

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def adapter(script: dict[str, Any]) -> SqlServerAdapter:
    instance = SqlServerAdapter(lambda _: ScriptedConnection(script))
    instance._catalog[REF.object_id] = REF
    return instance


@pytest.fixture(autouse=True)
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SQLCTX_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_every_feature_detail_uses_the_database_default_collation() -> None:
    branches = tables.FEATURES_QUERY.count("UNION ALL") + 1
    assert tables.FEATURES_QUERY.count("COLLATE DATABASE_DEFAULT") == branches


def test_base_features_query_needs_no_version_specific_column() -> None:
    assert "temporal_type" not in tables.FEATURES_QUERY
    assert "is_memory_optimized" not in tables.FEATURES_QUERY


@pytest.mark.parametrize(
    ("major", "edition", "expected"),
    [
        (10, 3, []),
        (12, 3, ["memory_optimized"]),
        (13, 2, ["memory_optimized", "temporal"]),
        (17, 3, ["memory_optimized", "temporal"]),
        (12, 5, ["memory_optimized", "temporal"]),
        (None, None, []),
    ],
)
def test_optional_probes_follow_server_version(
    major: int | None, edition: int | None, expected: list[str]
) -> None:
    assert tables.optional_probes(major, edition) == expected


def test_failed_optional_probe_becomes_an_unsupported_feature() -> None:
    features = adapter(
        {
            "'partitioned' as feature": (["feature", "detail"], [("data_compression", "PK:PAGE")]),
            "as major_version": (["major_version", "engine_edition"], [(16, 3)]),
            "is_memory_optimized = 1": ProgrammingError("42000", "[SQL Server]denied (229)"),
            "temporal_type <> 0": (["feature", "detail"], [("temporal", "system_versioned")]),
        }
    ).get_table_features(PROFILE, REF)
    assert [(item.feature, item.detail) for item in features] == [
        ("data_compression", "PK:PAGE"),
        ("temporal", "system_versioned"),
        ("probe_unavailable", "memory_optimized"),
    ]
    assert all(not item.rendered for item in features)


def test_unreadable_server_version_skips_probes_without_failing() -> None:
    features = adapter(
        {"as major_version": ProgrammingError("42000", "[SQL Server]denied (229)")}
    ).get_table_features(PROFILE, REF)
    assert [item.detail for item in features] == ["memory_optimized", "temporal"]


def test_metadata_failure_names_statement_and_writes_protected_diagnostic(
    runtime_root: Path,
) -> None:
    with pytest.raises(SqlCtxError) as caught:
        adapter(
            {"'partitioned' as feature": ProgrammingError(*COLLATION_ERROR)}
        ).get_table_features(PROFILE, REF)
    payload = caught.value.public_payload()
    assert payload["code"] == "DATABASE_OPERATION_FAILED"
    assert payload["statement"] == "table_features"
    assert payload["sql_error_numbers"] == [451, 8180]
    written = runtime_root / "errors" / f"{payload['correlation_id']}.json"
    diagnostic = json.loads(written.read_text(encoding="utf-8"))
    assert diagnostic["statement"] == "table_features"
    assert diagnostic["sqlstate"] == "42000"
    assert "Thai_CI_AS" in diagnostic["message"]
    text = written.read_text(encoding="utf-8")
    for secret in ("password", "localhost", "AUTH_FLOW", "agrimap_app"):
        assert secret not in text


def test_diagnostic_redacts_connection_fragments() -> None:
    diagnostic = database_error_diagnostic(
        RuntimeError("login failed for UID=sa;PWD=hunter2; [SQL Server]Login failed (18456)")
    )
    assert "hunter2" not in diagnostic["message"]
    assert diagnostic["native_errors"] == [18456]
