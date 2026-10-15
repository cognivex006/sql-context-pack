"""Lossless SQL Server table metadata: catalog queries and neutral T-SQL rendering."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlctx.core.enums import ConstraintType
from sqlctx.core.models import (
    ColumnMetadata,
    ComputedColumnMetadata,
    ConstraintMetadata,
    DefaultConstraintMetadata,
    ForeignKeyMetadata,
    IdentityMetadata,
    IndexMetadata,
    ObjectRef,
    TableFeatureMetadata,
)

COLUMNS_QUERY = """
    SELECT c.name AS column_name, t.name AS data_type,
           c.is_nullable, c.column_id AS ordinal_position,
           c.max_length, c.precision, c.scale,
           t.is_user_defined, ts.name AS type_schema,
           CASE WHEN c.collation_name <> CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS sysname)
                THEN c.collation_name END AS collation_name,
           CAST(idc.seed_value AS nvarchar(64)) AS identity_seed,
           CAST(idc.increment_value AS nvarchar(64)) AS identity_increment,
           cc.definition AS computed_definition, cc.is_persisted AS computed_persisted,
           dc.name AS default_name, dc.definition AS default_definition,
           dc.is_system_named AS default_system_named,
           c.is_sparse,
           CAST(ep.value AS nvarchar(max)) AS column_description
      FROM sys.columns c
      JOIN sys.types t ON t.user_type_id = c.user_type_id
      JOIN sys.schemas ts ON ts.schema_id = t.schema_id
      JOIN sys.objects o ON o.object_id = c.object_id
      JOIN sys.schemas s ON s.schema_id = o.schema_id
      LEFT JOIN sys.identity_columns idc
        ON idc.object_id = c.object_id AND idc.column_id = c.column_id
      LEFT JOIN sys.computed_columns cc
        ON cc.object_id = c.object_id AND cc.column_id = c.column_id
      LEFT JOIN sys.default_constraints dc
        ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
      LEFT JOIN sys.extended_properties ep
        ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id
       AND ep.name = 'MS_Description'
     WHERE s.name = ? AND o.name = ? ORDER BY c.column_id
"""

KEYS_QUERY = """
    WITH target AS (SELECT ? AS schema_name, ? AS object_name)
    SELECT i.name AS constraint_name,
           CASE WHEN i.is_primary_key = 1 THEN 'primary key' ELSE 'unique' END AS constraint_type,
           c.name AS column_name, CAST(NULL AS nvarchar(max)) AS expression,
           CASE WHEN i.type = 1 THEN 1 ELSE 0 END AS is_clustered,
           ic.is_descending_key, kc.is_system_named,
           CAST(1 AS bit) AS is_enabled, CAST(1 AS bit) AS is_trusted,
           ic.key_ordinal AS sort_order
      FROM sys.key_constraints kc
      JOIN sys.indexes i ON i.object_id = kc.parent_object_id AND i.index_id = kc.unique_index_id
      JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
      JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
      JOIN sys.objects o ON o.object_id = kc.parent_object_id
      JOIN sys.schemas s ON s.schema_id = o.schema_id
      JOIN target t ON t.schema_name = s.name AND t.object_name = o.name
     WHERE ic.key_ordinal > 0
    UNION ALL
    SELECT cc.name, 'check', CAST(NULL AS sysname), cc.definition,
           CAST(NULL AS int), CAST(0 AS bit), cc.is_system_named,
           CASE WHEN cc.is_disabled = 1 THEN 0 ELSE 1 END,
           CASE WHEN cc.is_not_trusted = 1 THEN 0 ELSE 1 END,
           0
      FROM sys.check_constraints cc
      JOIN sys.objects o ON o.object_id = cc.parent_object_id
      JOIN sys.schemas s ON s.schema_id = o.schema_id
      JOIN target t ON t.schema_name = s.name AND t.object_name = o.name
     ORDER BY constraint_name, sort_order
"""

FOREIGN_KEYS_QUERY = """
    SELECT fk.name AS constraint_name, pc.name AS source_column,
           rs.name AS target_schema, ro.name AS target_table, rc.name AS target_column,
           fk.delete_referential_action_desc AS on_delete,
           fk.update_referential_action_desc AS on_update,
           fk.is_disabled, fk.is_not_trusted, fkc.constraint_column_id
      FROM sys.foreign_keys fk
      JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
      JOIN sys.objects po ON po.object_id = fkc.parent_object_id
      JOIN sys.schemas ps ON ps.schema_id = po.schema_id
      JOIN sys.columns pc ON pc.object_id = po.object_id AND pc.column_id = fkc.parent_column_id
      JOIN sys.objects ro ON ro.object_id = fkc.referenced_object_id
      JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
      JOIN sys.columns rc ON rc.object_id = ro.object_id AND rc.column_id = fkc.referenced_column_id
     WHERE ps.name = ? AND po.name = ?
     ORDER BY fk.name, fkc.constraint_column_id
"""

INDEXES_QUERY = """
    SELECT i.name AS index_name, i.is_unique, i.is_primary_key AS is_primary,
           c.name AS column_name, ic.key_ordinal AS column_order,
           ic.is_included_column AS is_included, ic.is_descending_key,
           i.type_desc AS index_type, i.filter_definition,
           i.is_unique_constraint, ic.index_column_id
      FROM sys.indexes i
      JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
      JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
      JOIN sys.objects o ON o.object_id = i.object_id
      JOIN sys.schemas s ON s.schema_id = o.schema_id
     WHERE s.name = ? AND o.name = ? AND i.name IS NOT NULL AND i.is_hypothetical = 0
     ORDER BY i.name, ic.is_included_column, ic.key_ordinal, ic.index_column_id
"""

FEATURES_QUERY = """
    WITH target AS (
        SELECT tb.object_id, tb.temporal_type, tb.is_memory_optimized,
               tb.filestream_data_space_id
          FROM sys.tables tb JOIN sys.schemas s ON s.schema_id = tb.schema_id
         WHERE s.name = ? AND tb.name = ?
    )
    SELECT 'partitioned' AS feature, i.name AS detail
      FROM target t JOIN sys.indexes i ON i.object_id = t.object_id
      JOIN sys.data_spaces ds ON ds.data_space_id = i.data_space_id
     WHERE ds.type = 'PS'
    UNION ALL
    SELECT 'filegroup', CONCAT(COALESCE(i.name, '(heap)'), ':', ds.name)
      FROM target t JOIN sys.indexes i ON i.object_id = t.object_id
      JOIN sys.data_spaces ds ON ds.data_space_id = i.data_space_id
     WHERE ds.type = 'FG' AND ds.is_default = 0
    UNION ALL
    SELECT 'columnstore_index', i.name
      FROM target t JOIN sys.indexes i ON i.object_id = t.object_id WHERE i.type IN (5, 6)
    UNION ALL
    SELECT 'xml_or_spatial_index', i.name
      FROM target t JOIN sys.indexes i ON i.object_id = t.object_id WHERE i.type IN (3, 4)
    UNION ALL
    SELECT 'hash_index', i.name
      FROM target t JOIN sys.indexes i ON i.object_id = t.object_id WHERE i.type = 7
    UNION ALL
    SELECT 'data_compression', CONCAT(COALESCE(i.name, '(heap)'), ':', p.data_compression_desc)
      FROM target t JOIN sys.partitions p ON p.object_id = t.object_id
      LEFT JOIN sys.indexes i ON i.object_id = p.object_id AND i.index_id = p.index_id
     WHERE p.data_compression <> 0 AND p.partition_number = 1
    UNION ALL
    SELECT 'temporal', CASE t.temporal_type WHEN 1 THEN 'history_table'
                                           ELSE 'system_versioned' END
      FROM target t WHERE t.temporal_type <> 0
    UNION ALL
    SELECT 'memory_optimized', NULL FROM target t WHERE t.is_memory_optimized = 1
    UNION ALL
    SELECT 'filestream', NULL FROM target t WHERE t.filestream_data_space_id IS NOT NULL
    UNION ALL
    SELECT 'sparse_column', c.name
      FROM target t JOIN sys.columns c ON c.object_id = t.object_id WHERE c.is_sparse = 1
    UNION ALL
    SELECT 'column_set', c.name
      FROM target t JOIN sys.columns c ON c.object_id = t.object_id WHERE c.is_column_set = 1
     ORDER BY feature, detail
"""

RENDERED_FEATURES = frozenset({"sparse_column"})
_LENGTH_TYPES = {"varchar", "char", "varbinary", "binary"}
_UNICODE_TYPES = {"nvarchar", "nchar"}
_SCALE_TYPES = {"datetime2", "time", "datetimeoffset"}


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _text(value: Any) -> str | None:
    return None if value in {None, ""} else str(value)


def type_sql(
    name: str,
    max_length: int | None,
    precision: int | None,
    scale: int | None,
    *,
    user_defined: bool = False,
    type_schema: str | None = None,
    quote: Callable[[str], str] | None = None,
) -> str:
    """Return the declared SQL Server type including length, precision and scale."""
    if user_defined and quote is not None and type_schema:
        return f"{quote(type_schema)}.{quote(name)}"
    lowered = name.lower()
    if lowered in _LENGTH_TYPES and max_length is not None:
        return f"{lowered}({'max' if max_length == -1 else max_length})"
    if lowered in _UNICODE_TYPES and max_length is not None:
        return f"{lowered}({'max' if max_length == -1 else max_length // 2})"
    if lowered in {"decimal", "numeric"} and precision is not None:
        return f"{lowered}({precision},{scale or 0})"
    if lowered in _SCALE_TYPES and scale is not None:
        return f"{lowered}({scale})"
    if lowered == "float" and precision not in {None, 53}:
        return f"float({precision})"
    return lowered


def map_columns(
    rows: Sequence[Mapping[str, Any]], quote: Callable[[str], str]
) -> list[ColumnMetadata]:
    result: list[ColumnMetadata] = []
    for row in rows:
        data_type = str(row["data_type"])
        max_length, precision, scale = (
            _int(row.get("max_length")),
            _int(row.get("precision")),
            _int(row.get("scale")),
        )
        seed, increment = _text(row.get("identity_seed")), _text(row.get("identity_increment"))
        computed = _text(row.get("computed_definition"))
        default_name = _text(row.get("default_name"))
        result.append(
            ColumnMetadata(
                name=str(row["column_name"]),
                data_type=data_type,
                nullable=_bool(row.get("is_nullable", True)),
                ordinal=int(row["ordinal_position"]),
                type_sql=type_sql(
                    data_type,
                    max_length,
                    precision,
                    scale,
                    user_defined=_bool(row.get("is_user_defined")),
                    type_schema=_text(row.get("type_schema")),
                    quote=quote,
                ),
                max_length=max_length,
                precision=precision,
                scale=scale,
                collation=_text(row.get("collation_name")),
                identity=(
                    IdentityMetadata(seed=seed, increment=increment)
                    if seed is not None and increment is not None
                    else None
                ),
                computed=(
                    ComputedColumnMetadata(
                        definition=computed, persisted=_bool(row.get("computed_persisted"))
                    )
                    if computed is not None
                    else None
                ),
                default=(
                    DefaultConstraintMetadata(
                        name=default_name,
                        definition=str(row.get("default_definition") or ""),
                        system_named=_bool(row.get("default_system_named")),
                    )
                    if default_name is not None
                    else None
                ),
                sparse=_bool(row.get("is_sparse")),
                description=_text(row.get("column_description")),
            )
        )
    return result


def map_constraints(rows: Sequence[Mapping[str, Any]]) -> list[ConstraintMetadata]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        kind = str(row["constraint_type"]).lower()
        item = grouped.setdefault(
            (str(row["constraint_name"]), kind),
            {
                "columns": [],
                "orders": [],
                "expression": _text(row.get("expression")),
                "clustered": None
                if row.get("is_clustered") is None
                else _bool(row["is_clustered"]),
                "enabled": _bool(row.get("is_enabled", True)),
                "trusted": _bool(row.get("is_trusted", True)),
                "system_named": _bool(row.get("is_system_named")),
            },
        )
        if row.get("column_name") is not None:
            item["columns"].append(str(row["column_name"]))
            item["orders"].append("DESC" if _bool(row.get("is_descending_key")) else "ASC")
    types = {"primary key": ConstraintType.PRIMARY_KEY, "unique": ConstraintType.UNIQUE}
    return [
        ConstraintMetadata(
            name=name,
            constraint_type=types.get(kind, ConstraintType.CHECK),
            columns=value["columns"],
            expression=value["expression"],
            clustered=value["clustered"],
            column_orders=value["orders"],
            enabled=value["enabled"],
            trusted=value["trusted"],
            system_named=value["system_named"],
        )
        for (name, kind), value in grouped.items()
    ]


def map_foreign_keys(
    rows: Sequence[Mapping[str, Any]], source_object_id: str
) -> list[ForeignKeyMetadata]:
    grouped: dict[str, ForeignKeyMetadata] = {}
    for row in rows:
        name = str(row["constraint_name"])
        item = grouped.get(name)
        if item is None:
            item = grouped[name] = ForeignKeyMetadata(
                name=name,
                source_object_id=source_object_id,
                source_columns=[],
                target_object_id=f"table:{row['target_schema']}.{row['target_table']}",
                target_columns=[],
                on_delete=_text(row.get("on_delete")),
                on_update=_text(row.get("on_update")),
                enabled=not _bool(row.get("is_disabled")),
                trusted=not _bool(row.get("is_not_trusted")),
            )
        item.source_columns.append(str(row["source_column"]))
        item.target_columns.append(str(row["target_column"]))
    return list(grouped.values())


def map_indexes(rows: Sequence[Mapping[str, Any]]) -> list[IndexMetadata]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("index_name") in {None, ""}:
            continue
        index_type = _text(row.get("index_type"))
        item = grouped.setdefault(
            str(row["index_name"]),
            {
                "unique": _bool(row.get("is_unique")),
                "primary": _bool(row.get("is_primary")),
                "clustered": None
                if index_type is None
                else index_type.upper().startswith("CLUSTERED"),
                "index_type": index_type,
                "filter": _text(row.get("filter_definition")),
                "unique_constraint": _bool(row.get("is_unique_constraint")),
                "keys": [],
                "included": [],
            },
        )
        if row.get("column_name") is None:
            continue
        column = str(row["column_name"])
        if _bool(row.get("is_included")):
            item["included"].append(column)
        else:
            item["keys"].append((column, "DESC" if _bool(row.get("is_descending_key")) else "ASC"))
    return [
        IndexMetadata(
            name=name,
            unique=value["unique"],
            primary=value["primary"],
            key_columns=[column for column, _ in value["keys"]],
            included_columns=value["included"],
            clustered=value["clustered"],
            index_type=value["index_type"],
            key_orders=[order for _, order in value["keys"]],
            filter_definition=value["filter"],
            unique_constraint=value["unique_constraint"],
        )
        for name, value in grouped.items()
    ]


def map_features(rows: Sequence[Mapping[str, Any]]) -> list[TableFeatureMetadata]:
    return [
        TableFeatureMetadata(
            feature=str(row["feature"]),
            detail=_text(row.get("detail")),
            rendered=str(row["feature"]) in RENDERED_FEATURES,
        )
        for row in rows
    ]


def _literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def _action(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("_", " ").upper()


def render_table(
    ref: ObjectRef,
    *,
    columns: list[ColumnMetadata],
    constraints: list[ConstraintMetadata],
    foreign_keys: list[ForeignKeyMetadata],
    indexes: list[IndexMetadata],
    comment: str | None,
    features: list[TableFeatureMetadata],
    quote: Callable[[str], str],
) -> str:
    """Render standard T-SQL that recreates the table without product-specific layout."""
    table = f"{quote(ref.schema_name)}.{quote(ref.object_name)}"

    def keyed(names: list[str], orders: list[str]) -> str:
        return ", ".join(
            f"{quote(name)} {orders[position] if position < len(orders) else 'ASC'}"
            for position, name in enumerate(names)
        )

    entries: list[str] = []
    for column in sorted(columns, key=lambda item: item.ordinal):
        if column.computed is not None:
            persisted = " PERSISTED" if column.computed.persisted else ""
            not_null = " NOT NULL" if column.computed.persisted and not column.nullable else ""
            entries.append(
                f"    {quote(column.name)} AS {column.computed.definition}{persisted}{not_null}"
            )
            continue
        parts = [quote(column.name), column.type_sql or column.data_type]
        if column.sparse:
            parts.append("SPARSE")
        if column.collation:
            parts.append(f"COLLATE {column.collation}")
        if column.identity is not None:
            parts.append(f"IDENTITY({column.identity.seed},{column.identity.increment})")
        parts.append("NULL" if column.nullable else "NOT NULL")
        if column.default is not None:
            parts.append(
                f"CONSTRAINT {quote(column.default.name)} DEFAULT {column.default.definition}"
            )
        entries.append("    " + " ".join(parts))
    deferred_checks: list[ConstraintMetadata] = []
    for constraint in constraints:
        name = quote(constraint.name)
        clustered = (
            ""
            if constraint.clustered is None
            else (" CLUSTERED" if constraint.clustered else " NONCLUSTERED")
        )
        if constraint.constraint_type == ConstraintType.PRIMARY_KEY:
            entries.append(
                f"    CONSTRAINT {name} PRIMARY KEY{clustered} "
                f"({keyed(constraint.columns, constraint.column_orders)})"
            )
        elif constraint.constraint_type == ConstraintType.UNIQUE:
            entries.append(
                f"    CONSTRAINT {name} UNIQUE{clustered} "
                f"({keyed(constraint.columns, constraint.column_orders)})"
            )
        elif constraint.constraint_type == ConstraintType.CHECK and constraint.expression:
            if constraint.enabled is False or constraint.trusted is False:
                deferred_checks.append(constraint)
            else:
                entries.append(f"    CONSTRAINT {name} CHECK {constraint.expression}")
    statements = [f"CREATE TABLE {table} (\n" + ",\n".join(entries) + "\n);"]
    for constraint in deferred_checks:
        statements.append(
            f"ALTER TABLE {table} WITH NOCHECK ADD CONSTRAINT {quote(constraint.name)} "
            f"CHECK {constraint.expression};"
        )
        if constraint.enabled is False:
            statements.append(f"ALTER TABLE {table} NOCHECK CONSTRAINT {quote(constraint.name)};")
    for foreign_key in foreign_keys:
        target = foreign_key.target_object_id.removeprefix("table:").split(".", 1)
        if len(target) != 2:
            continue
        check = "WITH NOCHECK" if foreign_key.trusted is False else "WITH CHECK"
        statement = (
            f"ALTER TABLE {table} {check} ADD CONSTRAINT {quote(foreign_key.name)} "
            f"FOREIGN KEY ({', '.join(quote(item) for item in foreign_key.source_columns)}) "
            f"REFERENCES {quote(target[0])}.{quote(target[1])} "
            f"({', '.join(quote(item) for item in foreign_key.target_columns)})"
        )
        for clause, action in (
            ("ON DELETE", foreign_key.on_delete),
            ("ON UPDATE", foreign_key.on_update),
        ):
            rendered = _action(action)
            if rendered and rendered != "NO ACTION":
                statement += f" {clause} {rendered}"
        statements.append(statement + ";")
        if foreign_key.enabled is False:
            statements.append(f"ALTER TABLE {table} NOCHECK CONSTRAINT {quote(foreign_key.name)};")
    for index in indexes:
        if index.primary or index.unique_constraint or not index.key_columns:
            continue
        if index.index_type and index.index_type.upper() not in {"CLUSTERED", "NONCLUSTERED"}:
            continue
        unique = "UNIQUE " if index.unique else ""
        kind = (
            ""
            if index.clustered is None
            else ("CLUSTERED " if index.clustered else "NONCLUSTERED ")
        )
        statement = (
            f"CREATE {unique}{kind}INDEX {quote(index.name)} ON {table} "
            f"({keyed(index.key_columns, index.key_orders)})"
        )
        if index.included_columns:
            statement += f" INCLUDE ({', '.join(quote(item) for item in index.included_columns)})"
        if index.filter_definition:
            statement += f" WHERE {index.filter_definition}"
        statements.append(statement + ";")
    level = (
        f"@level0type = N'SCHEMA', @level0name = {_literal(ref.schema_name)}, "
        f"@level1type = N'TABLE', @level1name = {_literal(ref.object_name)}"
    )
    if comment:
        statements.append(
            "EXEC sys.sp_addextendedproperty @name = N'MS_Description', "
            f"@value = {_literal(comment)}, {level};"
        )
    for column in sorted(columns, key=lambda item: item.ordinal):
        if column.description:
            statements.append(
                "EXEC sys.sp_addextendedproperty @name = N'MS_Description', "
                f"@value = {_literal(column.description)}, {level}, "
                f"@level2type = N'COLUMN', @level2name = {_literal(column.name)};"
            )
    unrendered = [item for item in features if not item.rendered]
    if unrendered:
        lines = [
            f"-- not rendered: {item.feature}"
            + (f" ({' '.join(item.detail.split())})" if item.detail else "")
            for item in unrendered
        ]
        statements.insert(0, "\n".join(lines))
    return "\n\n".join(statements)
