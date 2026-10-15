"""Parse -> format -> re-parse with the pinned SQLFluff on a realistic sample per dialect."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sqlctx._version import SQLFLUFF_VERSION
from sqlctx.core.enums import FormatStatus
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.runtime import JsonRuntimeStateStore

SAMPLES = Path(__file__).parent / "format_samples"

# Text formatting must leave alone in each sample: meaning-bearing functions, identifier case,
# identifier quoting and dialect operators.
PRESERVED: dict[str, tuple[str, ...]] = {
    "tsql": ("ISNULL(", "[OrderId]", "CustomerId", "N'ทดสอบ'", "WITH (NOLOCK)"),
    "postgres": ('sales."OrderLines"', "::numeric(12,2)", "->>'kind'", "$$"),
    "mysql": ("IFNULL(", "`Orders`", "shop.Customers", "LIMIT 10, 20"),
    "mariadb": ("`CustomerId`", "AUTO_INCREMENT", "utf8mb4_unicode_ci"),
    "oracle": ("NVL(", 'o."OrderDate"', "l.order_id(+)", "'O''Brien'", "FETCH FIRST 10"),
}


@pytest.fixture(scope="module")
def formatter(tmp_path_factory: pytest.TempPathFactory) -> SqlFluffFormatter:
    state = JsonRuntimeStateStore(tmp_path_factory.mktemp("rt"))
    manager = SqlFluffManager(state, python_executable=Path(sys.executable))
    if manager.installed_version() != SQLFLUFF_VERSION:
        pytest.skip(f"pinned SQLFluff {SQLFLUFF_VERSION} is not installed")
    return SqlFluffFormatter(manager)


@pytest.mark.parametrize("dialect", sorted(PRESERVED))
def test_sample_formats_without_rolling_back(formatter: SqlFluffFormatter, dialect: str) -> None:
    sql = (SAMPLES / f"{dialect}.sql").read_text(encoding="utf-8")

    result = formatter.format_one(object_id=f"sample:{dialect}", sql=sql, dialect=dialect)

    assert result.status == FormatStatus.FORMATTED, result.diagnostics
    for fragment in PRESERVED[dialect]:
        assert fragment in result.content, fragment
    assert "COALESCE" not in result.content


def test_oracle_q_quote_is_preserved_as_a_parse_failure(formatter: SqlFluffFormatter) -> None:
    # SQLFluff 4.2.2 cannot lex Oracle alternative quoting; the original must come back intact.
    sql = "SELECT name FROM customers WHERE name <> q'[O'Brien]'\n"

    result = formatter.format_one(object_id="sample:q-quote", sql=sql, dialect="oracle")

    assert result.status == FormatStatus.PARSE_FAILED
    assert result.content == sql
