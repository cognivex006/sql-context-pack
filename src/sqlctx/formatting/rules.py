"""Per-dialect SQLFluff rules that formatting must never apply, because they change meaning."""

from __future__ import annotations

# Bump whenever a rule set below changes, so cached results and tooling fingerprints refresh.
FORMAT_RULE_POLICY = "dialect-rules-v1"

# Fallback when no dialect is given and no profile names an engine.
DEFAULT_FORMAT_DIALECT = "tsql"

# CP02 recases identifiers: MySQL/MariaDB table names and case-sensitive SQL Server collations
#      resolve differently.
# CV02 rewrites NVL/IFNULL/ISNULL to COALESCE, which changes result type and evaluation.
# RF06 drops "unnecessary" identifier quotes, which changes case folding on PostgreSQL/Oracle.
_SEMANTIC_RULES: tuple[str, ...] = ("CP02", "CV02", "RF06")

# LT01 is also excluded where its spacing fixes are not safe: T-SQL (tuned since 2.1.0) and
# Oracle, where it splits the outer-join marker `col(+)` into `col (+)`.
_EXCLUDED_RULES: dict[str, tuple[str, ...]] = {
    "ansi": ("CP02", "CV02", "LT01", "RF06"),
    "tsql": ("CP02", "CV02", "LT01", "RF06"),
    "postgres": _SEMANTIC_RULES,
    "mysql": _SEMANTIC_RULES,
    "mariadb": _SEMANTIC_RULES,
    "oracle": ("CP02", "CV02", "LT01", "RF06"),
}


def excluded_rules(dialect: str) -> tuple[str, ...]:
    """Return the rules formatting excludes for a dialect; unknown dialects get the widest set."""
    return _EXCLUDED_RULES.get(dialect, _EXCLUDED_RULES["ansi"])
