"""Requirement v1.3: connection-string, read-only login, locking-read and diagnostics hardening."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from sqlctx.adapters.mariadb import MariaDbAdapter
from sqlctx.adapters.mysql import MySqlAdapter
from sqlctx.adapters.oracle import OracleAdapter
from sqlctx.adapters.postgres import PostgreSqlAdapter
from sqlctx.adapters.registry import _connection_factory, _odbc_value
from sqlctx.core.enums import DatabaseEngine, ObjectType, OracleConnectMode
from sqlctx.core.errors import SqlCtxError, ToolingUnavailable
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile
from sqlctx.formatting.formatter import _sanitized_diagnostics
from sqlctx.security.profiles import ProfileDefinition

NON_SQLSERVER_ADAPTERS = [PostgreSqlAdapter, MySqlAdapter, MariaDbAdapter, OracleAdapter]


def resolved(
    engine: DatabaseEngine,
    *,
    password: str | None = None,
    database: str = "demo",
    oracle_connect_mode: OracleConnectMode = OracleConnectMode.SERVICE_NAME,
) -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=engine,
        host="db.local",
        port=1521,
        database=database,
        username="reader",
        password=password or "fixture-only",
        allowed_schemas=("app",),
        allowed_object_types=(ObjectType.TABLE,),
        oracle_connect_mode=oracle_connect_mode,
    )


def table_ref(engine: DatabaseEngine) -> ObjectRef:
    return ObjectRef(
        object_id="table:app.UM_USER",
        engine=engine.value,
        schema_name="app",
        object_name="UM_USER",
        object_type="table",
    )


class _Connection:
    def close(self) -> None: ...


def test_odbc_values_are_braced_so_a_password_cannot_inject_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    fake_pyodbc = SimpleNamespace(
        Error=Exception,
        drivers=lambda: ["ODBC Driver 18 for SQL Server"],
        connect=lambda connection_string, **_: captured.append(connection_string) or _Connection(),
    )
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)

    hostile = "x;Encrypt=no;TrustServerCertificate=yes;}"
    _connection_factory(DatabaseEngine.SQLSERVER)(
        resolved(DatabaseEngine.SQLSERVER, password=hostile)
    )

    assert "PWD={x;Encrypt=no;TrustServerCertificate=yes;}}};" in captured[0]
    assert captured[0].endswith("Encrypt=yes;TrustServerCertificate=no;Connection Timeout=10;")
    assert captured[0].count("Encrypt=") == 2  # the real one, plus the inert text in PWD


def test_odbc_value_rejects_nul() -> None:
    with pytest.raises(ToolingUnavailable):
        _odbc_value("a\x00b")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (OracleConnectMode.SERVICE_NAME, ("db.local", 1521, {"service_name": "ORCLPDB"})),
        (OracleConnectMode.SID, ("db.local", 1521, {"sid": "ORCLPDB"})),
        (OracleConnectMode.TNS_ALIAS, None),
    ],
)
def test_oracle_dsn_follows_the_declared_connect_mode(
    monkeypatch: pytest.MonkeyPatch, mode: OracleConnectMode, expected: Any
) -> None:
    made: list[Any] = []
    connected: list[str] = []

    def makedsn(host: str, port: int, **kwargs: str) -> str:
        made.append((host, port, kwargs))
        return "built-dsn"

    fake_oracledb = SimpleNamespace(
        makedsn=makedsn,
        connect=lambda **kwargs: connected.append(kwargs["dsn"]) or _Connection(),
    )
    monkeypatch.setitem(sys.modules, "oracledb", fake_oracledb)

    _connection_factory(DatabaseEngine.ORACLE)(
        resolved(DatabaseEngine.ORACLE, database="ORCLPDB", oracle_connect_mode=mode)
    )

    if expected is None:
        assert made == [] and connected == ["ORCLPDB"]
    else:
        assert made == [expected] and connected == ["built-dsn"]


def test_oracle_connect_mode_is_refused_on_other_engines() -> None:
    base = {
        "credential_ref": "x",
        "port": 5432,
        "allowed_schemas": ["app"],
        "allowed_object_types": ["table"],
        "oracle_connect_mode": "sid",
    }
    with pytest.raises(ValueError, match="only for oracle"):
        ProfileDefinition.model_validate({**base, "engine": "postgres"})
    assert ProfileDefinition.model_validate({**base, "engine": "oracle"}).oracle_connect_mode == (
        OracleConnectMode.SID
    )


def test_profile_without_oracle_connect_mode_keeps_service_name_default() -> None:
    document = yaml.safe_load(
        "engine: oracle\ncredential_ref: x\nport: 1521\n"
        "allowed_schemas: [app]\nallowed_object_types: [table]\n"
    )
    assert ProfileDefinition.model_validate(document).oracle_connect_mode is None
    assert resolved(DatabaseEngine.ORACLE).oracle_connect_mode == OracleConnectMode.SERVICE_NAME


@pytest.mark.parametrize("adapter_type", NON_SQLSERVER_ADAPTERS)
def test_every_engine_declares_both_write_capability_queries(adapter_type: type) -> None:
    assert adapter_type.queries.write_capability_database
    assert adapter_type.queries.write_capability_object


@pytest.mark.parametrize("adapter_type", NON_SQLSERVER_ADAPTERS)
@pytest.mark.parametrize(
    ("database_row", "object_row", "allowed"),
    [
        ({"a": 0, "b": False}, {"c": 0, "d": False}, True),
        ({"a": 1, "b": False}, {"c": 0, "d": False}, False),
        ({"a": 0, "b": False}, {"c": 0, "d": True}, False),
        ({"a": 0, "b": None}, {"c": 0, "d": False}, False),
        ({"a": 0, "b": False}, None, False),
    ],
)
def test_read_only_gate_fails_closed_unless_proven(
    monkeypatch: pytest.MonkeyPatch,
    adapter_type: type,
    database_row: dict[str, Any],
    object_row: dict[str, Any] | None,
    allowed: bool,
) -> None:
    adapter = adapter_type(lambda _: _Connection())
    responses = [[database_row], [object_row] if object_row is not None else []]
    monkeypatch.setattr(adapter, "_execute", lambda *args, **kwargs: responses.pop(0))
    profile = resolved(adapter.engine)

    if allowed:
        adapter.assert_query_read_only(profile, (table_ref(adapter.engine),))
        assert responses == []
    else:
        with pytest.raises(SqlCtxError) as caught:
            adapter.assert_query_read_only(profile, (table_ref(adapter.engine),))
        assert caught.value.code == "QUERY_READ_ONLY_CONTEXT_REQUIRED"


def test_read_only_gate_refuses_an_engine_without_capability_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PostgreSqlAdapter(lambda _: _Connection())
    monkeypatch.setattr(
        adapter,
        "queries",
        adapter.queries.__class__(**{**adapter.queries.__dict__, "write_capability_object": None}),
    )
    with pytest.raises(SqlCtxError) as caught:
        adapter.assert_query_read_only(resolved(DatabaseEngine.POSTGRES), ())
    assert caught.value.code == "QUERY_READ_ONLY_CONTEXT_REQUIRED"


def test_diagnostics_redact_literals_and_secrets_but_stay_traceable() -> None:
    raw = (
        "L:   3 | P:  12 | PRS | Line 3, Position 12: Found unparsable section: "
        "\"WHERE NAME = N'สมชาย ใจดี'\" password=hunter2"
    )
    lines = _sanitized_diagnostics(subprocess.CompletedProcess([], 1, "", raw))

    joined = " ".join(lines)
    assert "สมชาย" not in joined and "hunter2" not in joined
    assert "L:   3 | P:  12 | PRS" in joined and "Line 3, Position 12" in joined
    assert "password=[REDACTED]" in joined
    assert lines[-1].startswith("diagnostic fingerprint: ") and len(lines[-1]) == 36


def test_diagnostics_report_truncation_instead_of_hiding_it() -> None:
    raw = "PRS " + "x" * 2000
    lines = _sanitized_diagnostics(subprocess.CompletedProcess([], 1, "", raw))
    assert len(lines[0]) == 512
    assert lines[1] == "diagnostic truncated: showing 512 of 2004 characters"


def test_empty_diagnostics_stay_empty() -> None:
    assert _sanitized_diagnostics(subprocess.CompletedProcess([], 1, "", "")) == []
