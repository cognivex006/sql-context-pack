"""Oracle DDL queries must survive python-oracledb positional binding.

python-oracledb documents that binding by position "works from left to right and pays no
attention to the name of the bind variable". The adapter passes (schema, name), so the first
placeholder to appear must be the schema, whatever number it carries.
"""

from __future__ import annotations

import re

import pytest

from sqlctx.adapters.oracle import OracleAdapter

_PLACEHOLDER = re.compile(r":\w+")


def _bind_left_to_right(sql: str, values: tuple[str, ...]) -> str:
    """Substitute values as python-oracledb does for SQL: by order of appearance."""
    placeholders = _PLACEHOLDER.findall(sql)
    assert len(placeholders) == len(values)
    iterator = iter(values)
    return _PLACEHOLDER.sub(lambda _: f"'{next(iterator)}'", sql)


@pytest.mark.parametrize(
    ("attribute", "object_type"),
    [
        ("table_definition", "TABLE"),
        ("procedure_definition", "PROCEDURE"),
        ("function_definition", "FUNCTION"),
    ],
)
def test_get_ddl_receives_name_then_schema(attribute: str, object_type: str) -> None:
    adapter = OracleAdapter(connection_factory=lambda _profile: None)
    sql = getattr(adapter.queries, attribute)
    bound = _bind_left_to_right(sql, adapter._parameters("HR", "EMPLOYEES"))

    assert f"GET_DDL('{object_type}', object_name, owner)" in bound
    assert "'HR' AS owner" in bound
    assert "'EMPLOYEES' AS object_name" in bound
