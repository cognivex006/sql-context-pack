from __future__ import annotations

from typing import Any

import pytest

from sqlctx.adapters.sqlserver.adapter import (
    _METADATA_CONTEXT_CHECKS,
    _METADATA_CONTEXT_COLUMNS,
    _METADATA_CONTEXT_DEFAULTS,
    _METADATA_CONTEXT_INDEXES,
    SqlServerAdapter,
    _metadata_check_signature,
)
from sqlctx.context_index.contracts import ContextIndexEntry
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ResolvedConnectionProfile


def _profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.SQLSERVER,
        host="host",
        port=1433,
        database="db",
        username="user",
        password="secret",
        allowed_schemas=("dbo",),
        allowed_object_types=(ObjectType.TABLE, ObjectType.FUNCTION),
        metadata_context_write=True,
    )


def _rows() -> tuple[list[dict[str, Any]], ...]:
    columns = [
        {
            "column_name": name,
            "data_type": signature[0],
            "max_length": signature[1],
            "precision": signature[2],
            "scale": signature[3],
            "is_nullable": signature[4],
            "is_identity": signature[5],
            "is_computed": False,
            "is_rowguidcol": False,
            "is_sparse": False,
            "seed_value": "1" if name == "ID" else None,
            "increment_value": "1" if name == "ID" else None,
        }
        for name, signature in _METADATA_CONTEXT_COLUMNS.items()
    ]
    defaults = [
        {"constraint_name": name, "column_name": column, "definition": definition}
        for name, (column, definition) in _METADATA_CONTEXT_DEFAULTS.items()
    ]
    checks = [
        {
            "constraint_name": name,
            "definition": definition,
            "is_disabled": False,
            "is_not_trusted": False,
            "is_not_for_replication": False,
        }
        for name, definition in _METADATA_CONTEXT_CHECKS.items()
    ]
    indexes = [
        {
            "index_name": name,
            "is_unique": signature[0],
            "is_primary_key": signature[1],
            "is_unique_constraint": signature[2],
            "type_desc": signature[3],
            "is_disabled": False,
            "filter_definition": signature[4],
            "column_name": column,
            "key_ordinal": ordinal,
            "is_included_column": included,
            "is_descending_key": descending,
        }
        for name, signature in _METADATA_CONTEXT_INDEXES.items()
        for column, ordinal, included, descending in signature[5]
    ]
    return columns, defaults, checks, indexes, []


def test_metadata_context_schema_verification_accepts_full_signature() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    batches = iter(_rows())
    adapter._execute = lambda *_args, **_kwargs: next(batches)  # type: ignore[method-assign]

    adapter.verify_metadata_context_schema(_profile())


def test_metadata_context_schema_verification_rejects_wrong_type_and_version_check() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    columns, defaults, checks, indexes, auxiliary = _rows()
    next(item for item in columns if item["column_name"] == "ID")["data_type"] = "int"
    next(
        item
        for item in checks
        if item["constraint_name"] == "CK_DB_METADATA_CONTEXT_OUTPUT_VERSION"
    )["definition"] = "([OUTPUT_FORMAT_VERSION]=(99))"
    next(item for item in indexes if item["index_name"] == "IX_DB_METADATA_CONTEXT_LOOKUP_ACTIVE")[
        "is_disabled"
    ] = True
    next(item for item in indexes if item["index_name"] == "PK_DB_METADATA_CONTEXT")[
        "is_descending_key"
    ] = True
    defaults.append(
        {
            "constraint_name": "DF_DB_METADATA_CONTEXT_UNEXPECTED",
            "column_name": "DESCRIPTION",
            "definition": "N'blocked'",
        }
    )
    checks.append(
        {
            "constraint_name": "CK_DB_METADATA_CONTEXT_UNEXPECTED",
            "definition": "OBJECT_TYPE = 'TABLE'",
            "is_disabled": False,
            "is_not_trusted": False,
            "is_not_for_replication": False,
        }
    )
    indexes.append(
        {
            "index_name": "UX_DB_METADATA_CONTEXT_UNEXPECTED",
            "is_unique": True,
            "is_primary_key": False,
            "is_unique_constraint": False,
            "type_desc": "NONCLUSTERED",
            "is_disabled": False,
            "filter_definition": None,
            "column_name": "SCHEMA_NAME",
            "key_ordinal": 1,
            "is_included_column": False,
            "is_descending_key": True,
        }
    )
    auxiliary.append({"object_kind": "TRIGGER", "object_name": "TR_METADATA_CONTEXT"})
    batches = iter((columns, defaults, checks, indexes, auxiliary))
    adapter._execute = lambda *_args, **_kwargs: next(batches)  # type: ignore[method-assign]

    with pytest.raises(SqlCtxError) as caught:
        adapter.verify_metadata_context_schema(_profile())

    assert caught.value.code == "METADATA_CONTEXT_SCHEMA_DRIFT"
    assert "ID" in caught.value.details["column_mismatches"]
    assert "CK_DB_METADATA_CONTEXT_OUTPUT_VERSION" in caught.value.details["check_mismatches"]
    assert "IX_DB_METADATA_CONTEXT_LOOKUP_ACTIVE" in caught.value.details["index_mismatches"]
    assert "PK_DB_METADATA_CONTEXT" in caught.value.details["index_mismatches"]
    assert "DF_DB_METADATA_CONTEXT_UNEXPECTED" in caught.value.details["default_mismatches"]
    assert "CK_DB_METADATA_CONTEXT_UNEXPECTED" in caught.value.details["check_mismatches"]
    assert "UX_DB_METADATA_CONTEXT_UNEXPECTED" in caught.value.details["index_mismatches"]
    assert "TRIGGER:TR_METADATA_CONTEXT" in caught.value.details["unexpected_objects"]


# Definitions exactly as sys.check_constraints returned them on a SQL Server instance where
# the packaged DDL was applied; SQL Server rewrites IN, BETWEEN and NOT LIKE on storage.
_SERVER_RENDERED_CHECKS = {
    "CK_DB_METADATA_CONTEXT_CLASSIFICATION": (
        "([CLASSIFICATION_STATUS]='CONFIRMED' AND [CONTEXT_CODE] IS NOT NULL AND "
        "([CLASSIFICATION_SOURCE]='RULE' OR [CLASSIFICATION_SOURCE]='OWNER') OR "
        "[CLASSIFICATION_STATUS]='UNRESOLVED' AND [CONTEXT_CODE] IS NULL AND "
        "[CLASSIFICATION_SOURCE]='UNKNOWN' AND [TAGS_JSON]=N'[]')"
    ),
    "CK_DB_METADATA_CONTEXT_CONTEXT_CODE": (
        "([CONTEXT_CODE] IS NULL OR len([CONTEXT_CODE])>=(1) AND len([CONTEXT_CODE])<=(64) "
        "AND NOT ([CONTEXT_CODE]) collate Latin1_General_100_BIN2 like '%[^a-z0-9_-]%')"
    ),
    "CK_DB_METADATA_CONTEXT_EVIDENCE_JSON": (
        "(isjson([EVIDENCE_JSON])=(1) AND left(ltrim([EVIDENCE_JSON]),(1))=N'[')"
    ),
    "CK_DB_METADATA_CONTEXT_HEADER_VERSION": "([HEADER_VERSION]=(1))",
    "CK_DB_METADATA_CONTEXT_OBJECT_TYPE": (
        "([OBJECT_TYPE]='FUNCTION' OR [OBJECT_TYPE]='PROCEDURE' OR [OBJECT_TYPE]='TABLE')"
    ),
    "CK_DB_METADATA_CONTEXT_OUTPUT_VERSION": "([OUTPUT_FORMAT_VERSION]=(2))",
    "CK_DB_METADATA_CONTEXT_SOURCE": (
        "([CLASSIFICATION_SOURCE]='UNKNOWN' OR [CLASSIFICATION_SOURCE]='RULE' OR "
        "[CLASSIFICATION_SOURCE]='OWNER')"
    ),
    "CK_DB_METADATA_CONTEXT_STATUS": (
        "([CLASSIFICATION_STATUS]='UNRESOLVED' OR [CLASSIFICATION_STATUS]='CONFIRMED')"
    ),
    "CK_DB_METADATA_CONTEXT_TAGS_JSON": (
        "(isjson([TAGS_JSON])=(1) AND left(ltrim([TAGS_JSON]),(1))=N'[')"
    ),
}


def _server_rendered_rows() -> tuple[list[dict[str, Any]], ...]:
    columns, defaults, checks, indexes, auxiliary = _rows()
    for item in checks:
        item["definition"] = _SERVER_RENDERED_CHECKS[item["constraint_name"]]
    return columns, defaults, checks, indexes, auxiliary


def _verify(rows: tuple[list[dict[str, Any]], ...]) -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    batches = iter(rows)
    adapter._execute = lambda *_args, **_kwargs: next(batches)  # type: ignore[method-assign]
    adapter.verify_metadata_context_schema(_profile())


def test_server_rendered_checks_cover_the_whole_contract() -> None:
    assert set(_SERVER_RENDERED_CHECKS) == set(_METADATA_CONTEXT_CHECKS)


@pytest.mark.parametrize("name", sorted(_METADATA_CONTEXT_CHECKS))
def test_check_signature_matches_server_rendering(name: str) -> None:
    assert _metadata_check_signature(_SERVER_RENDERED_CHECKS[name]) == _metadata_check_signature(
        _METADATA_CONTEXT_CHECKS[name]
    )


def test_metadata_context_schema_verification_accepts_server_rendered_checks() -> None:
    _verify(_server_rendered_rows())


@pytest.mark.parametrize(
    ("name", "definition"),
    [
        # A value added to, removed from or changed in an IN list.
        (
            "CK_DB_METADATA_CONTEXT_OBJECT_TYPE",
            "([OBJECT_TYPE]='FUNCTION' OR [OBJECT_TYPE]='PROCEDURE' OR [OBJECT_TYPE]='VIEW')",
        ),
        ("CK_DB_METADATA_CONTEXT_STATUS", "([CLASSIFICATION_STATUS]='CONFIRMED')"),
        (
            "CK_DB_METADATA_CONTEXT_SOURCE",
            "([CLASSIFICATION_SOURCE]='UNKNOWN' OR [CLASSIFICATION_SOURCE]='RULE' OR "
            "[CLASSIFICATION_SOURCE]='OWNER' OR [CLASSIFICATION_SOURCE]='GUESS')",
        ),
        # Literal case is significant under a case-sensitive collation.
        (
            "CK_DB_METADATA_CONTEXT_STATUS",
            "([CLASSIFICATION_STATUS]='unresolved' OR [CLASSIFICATION_STATUS]='CONFIRMED')",
        ),
        # BETWEEN bounds widened.
        (
            "CK_DB_METADATA_CONTEXT_CONTEXT_CODE",
            "([CONTEXT_CODE] IS NULL OR len([CONTEXT_CODE])>=(1) AND len([CONTEXT_CODE])<=(128) "
            "AND NOT ([CONTEXT_CODE]) collate Latin1_General_100_BIN2 like '%[^a-z0-9_-]%')",
        ),
        # NOT LIKE turned into LIKE.
        (
            "CK_DB_METADATA_CONTEXT_CONTEXT_CODE",
            "([CONTEXT_CODE] IS NULL OR len([CONTEXT_CODE])>=(1) AND len([CONTEXT_CODE])<=(64) "
            "AND ([CONTEXT_CODE]) collate Latin1_General_100_BIN2 like '%[^a-z0-9_-]%')",
        ),
        # Binary collation replaced by a case-insensitive one.
        (
            "CK_DB_METADATA_CONTEXT_CONTEXT_CODE",
            "([CONTEXT_CODE] IS NULL OR len([CONTEXT_CODE])>=(1) AND len([CONTEXT_CODE])<=(64) "
            "AND NOT ([CONTEXT_CODE]) collate SQL_Latin1_General_CP1_CI_AS "
            "like '%[^a-z0-9_-]%')",
        ),
        # Same tokens, different precedence: the OR now spans the whole predicate.
        (
            "CK_DB_METADATA_CONTEXT_CLASSIFICATION",
            "([CLASSIFICATION_STATUS]='CONFIRMED' AND [CONTEXT_CODE] IS NOT NULL AND "
            "[CLASSIFICATION_SOURCE]='RULE' OR [CLASSIFICATION_SOURCE]='OWNER' OR "
            "[CLASSIFICATION_STATUS]='UNRESOLVED' AND [CONTEXT_CODE] IS NULL AND "
            "[CLASSIFICATION_SOURCE]='UNKNOWN' AND [TAGS_JSON]=N'[]')",
        ),
        # IS NOT NULL weakened to IS NULL.
        (
            "CK_DB_METADATA_CONTEXT_CLASSIFICATION",
            "([CLASSIFICATION_STATUS]='CONFIRMED' AND [CONTEXT_CODE] IS NULL AND "
            "([CLASSIFICATION_SOURCE]='RULE' OR [CLASSIFICATION_SOURCE]='OWNER') OR "
            "[CLASSIFICATION_STATUS]='UNRESOLVED' AND [CONTEXT_CODE] IS NULL AND "
            "[CLASSIFICATION_SOURCE]='UNKNOWN' AND [TAGS_JSON]=N'[]')",
        ),
        # Comparison operator changed.
        ("CK_DB_METADATA_CONTEXT_HEADER_VERSION", "([HEADER_VERSION]>=(1))"),
        # Function argument changed.
        (
            "CK_DB_METADATA_CONTEXT_TAGS_JSON",
            "(isjson([TAGS_JSON])=(1) AND left(rtrim([TAGS_JSON]),(1))=N'[')",
        ),
        # A conjunct dropped.
        ("CK_DB_METADATA_CONTEXT_EVIDENCE_JSON", "(isjson([EVIDENCE_JSON])=(1))"),
        # Text outside the parsed subset never matches the contract.
        ("CK_DB_METADATA_CONTEXT_HEADER_VERSION", "([HEADER_VERSION]=(1) + 0)"),
    ],
)
def test_metadata_context_schema_verification_rejects_semantic_check_drift(
    name: str, definition: str
) -> None:
    columns, defaults, checks, indexes, auxiliary = _server_rendered_rows()
    next(item for item in checks if item["constraint_name"] == name)["definition"] = definition

    with pytest.raises(SqlCtxError) as caught:
        _verify((columns, defaults, checks, indexes, auxiliary))

    assert caught.value.code == "METADATA_CONTEXT_SCHEMA_DRIFT"
    assert caught.value.details["check_mismatches"] == [name]


@pytest.mark.parametrize("flag", ["is_disabled", "is_not_trusted", "is_not_for_replication"])
def test_metadata_context_schema_verification_rejects_check_state_flags(flag: str) -> None:
    columns, defaults, checks, indexes, auxiliary = _server_rendered_rows()
    name = "CK_DB_METADATA_CONTEXT_STATUS"
    next(item for item in checks if item["constraint_name"] == name)[flag] = True

    with pytest.raises(SqlCtxError) as caught:
        _verify((columns, defaults, checks, indexes, auxiliary))

    assert caught.value.details["check_mismatches"] == [name]


def test_metadata_upsert_reports_semantic_unchanged() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    adapter._execute_write_returning = lambda *_args, **_kwargs: [  # type: ignore[method-assign]
        {"action": "UPDATE", "owner_values_preserved": 1, "semantic_changed": 0}
    ]
    entry = ContextIndexEntry(
        schema_name="dbo",
        object_name="APP_STATE",
        object_type=ObjectType.TABLE,
        context="app_state",
        tags=["app_state"],
        classification_status="confirmed",
        classification_source="owner",
    )

    action, preserved = adapter.upsert_metadata_context(_profile(), entry, actor_id=42)

    assert action == "unchanged"
    assert preserved is True


def test_complete_scope_deactivation_returns_only_safe_identities() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    captured: dict[str, Any] = {}

    def execute(_profile: object, query: str, parameters: object) -> list[dict[str, str]]:
        captured["query"] = query
        captured["parameters"] = parameters
        return [
            {
                "schema_name": "dbo",
                "object_type": "FUNCTION",
                "object_name": "REMOVED_FUNCTION",
            }
        ]

    adapter._execute_write_returning = execute  # type: ignore[method-assign]

    deactivated = adapter.deactivate_missing_metadata_context(
        _profile(),
        schemas=("dbo",),
        object_types=(ObjectType.TABLE, ObjectType.FUNCTION),
        present_identities=[("dbo", ObjectType.TABLE, "APP_STATE")],
        actor_id=42,
    )

    assert deactivated == [("dbo", "FUNCTION", "REMOVED_FUNCTION")]
    assert "OPENJSON(?)" in captured["query"]
    assert "APP_STATE" in str(captured["parameters"])
