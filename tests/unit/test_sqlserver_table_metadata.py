from __future__ import annotations

import re
from typing import Any

from sqlctx.adapters.sqlserver import SqlServerAdapter
from sqlctx.adapters.sqlserver.tables import type_sql
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile

COLUMN_FIELDS = (
    "column_name data_type is_nullable ordinal_position max_length precision scale "
    "is_user_defined type_schema collation_name identity_seed identity_increment "
    "computed_definition computed_persisted default_name default_definition "
    "default_system_named is_sparse column_description"
).split()

FIXTURE: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {
    "identity_seed": (
        COLUMN_FIELDS,
        [
            (
                "ORDER_ITEM_ID",
                "bigint",
                False,
                1,
                8,
                19,
                0,
                False,
                "sys",
                None,
                "1",
                "1",
                None,
                None,
                None,
                None,
                None,
                False,
                "รหัสรายการสั่งซื้อ",
            ),
            (
                "ORDER_ID",
                "int",
                False,
                2,
                4,
                10,
                0,
                False,
                "sys",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
                "รหัสคำสั่งซื้อ",
            ),
            (
                "ITEM_CODE",
                "nvarchar",
                False,
                3,
                100,
                0,
                0,
                False,
                "sys",
                "Thai_CI_AS",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
                "รหัสสินค้า",
            ),
            (
                "NOTE",
                "nvarchar",
                True,
                4,
                -1,
                0,
                0,
                False,
                "sys",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
                None,
            ),
            (
                "QTY",
                "numeric",
                False,
                5,
                17,
                38,
                0,
                False,
                "sys",
                None,
                None,
                None,
                None,
                None,
                "DF__ORDER_ITE__QTY__1A2B3C",
                "((0))",
                True,
                False,
                "จำนวน",
            ),
            (
                "UNIT_PRICE",
                "decimal",
                False,
                6,
                9,
                18,
                2,
                False,
                "sys",
                None,
                None,
                None,
                None,
                None,
                "DF_ORDER_ITEM_UNIT_PRICE",
                "((0.00))",
                False,
                False,
                None,
            ),
            (
                "LINE_TOTAL",
                "decimal",
                True,
                7,
                17,
                38,
                2,
                False,
                "sys",
                None,
                None,
                None,
                "([QTY]*[UNIT_PRICE])",
                True,
                None,
                None,
                None,
                False,
                "ยอดรวม",
            ),
            (
                "CREATED_AT",
                "datetime2",
                False,
                8,
                8,
                27,
                7,
                False,
                "sys",
                None,
                None,
                None,
                None,
                None,
                "DF_ORDER_ITEM_CREATED_AT",
                "(sysdatetime())",
                False,
                False,
                None,
            ),
            (
                "DEL_FLAG",
                "bit",
                False,
                9,
                1,
                1,
                0,
                False,
                "sys",
                None,
                None,
                None,
                None,
                None,
                "DF_ORDER_ITEM_DEL_FLAG",
                "((0))",
                False,
                False,
                None,
            ),
        ],
    ),
    "sys.key_constraints": (
        "constraint_name constraint_type column_name expression is_clustered "
        "is_descending_key is_system_named is_enabled is_trusted sort_order".split(),
        [
            ("CK_ORDER_ITEM_QTY", "check", None, "([QTY]>=(0))", None, False, False, True, True, 0),
            ("PK_ORDER_ITEM", "primary key", "ORDER_ITEM_ID", None, 1, False, False, True, True, 1),
            (
                "UQ_ORDER_ITEM_ORDER_CODE",
                "unique",
                "ORDER_ID",
                None,
                0,
                False,
                False,
                True,
                True,
                1,
            ),
            (
                "UQ_ORDER_ITEM_ORDER_CODE",
                "unique",
                "ITEM_CODE",
                None,
                0,
                True,
                False,
                True,
                True,
                2,
            ),
        ],
    ),
    "referential_action": (
        "constraint_name source_column target_schema target_table target_column on_delete "
        "on_update is_disabled is_not_trusted constraint_column_id".split(),
        [
            (
                "FK_ORDER_ITEM_ORDER",
                "ORDER_ID",
                "dbo",
                "ORDER_HEAD",
                "ORDER_ID",
                "CASCADE",
                "NO_ACTION",
                False,
                False,
                1,
            ),
            (
                "FK_ORDER_ITEM_ITEM",
                "ORDER_ID",
                "dbo",
                "ORDER_LINE_REF",
                "ORDER_ID",
                "NO_ACTION",
                "NO_ACTION",
                False,
                True,
                1,
            ),
            (
                "FK_ORDER_ITEM_ITEM",
                "ITEM_CODE",
                "dbo",
                "ORDER_LINE_REF",
                "ITEM_CODE",
                "NO_ACTION",
                "NO_ACTION",
                False,
                True,
                2,
            ),
        ],
    ),
    "filter_definition": (
        "index_name is_unique is_primary column_name column_order is_included "
        "is_descending_key index_type filter_definition is_unique_constraint "
        "index_column_id".split(),
        [
            (
                "IX_ORDER_ITEM_ACTIVE",
                False,
                False,
                "ORDER_ID",
                1,
                False,
                False,
                "NONCLUSTERED",
                "([DEL_FLAG]=(0))",
                False,
                1,
            ),
            (
                "IX_ORDER_ITEM_ACTIVE",
                False,
                False,
                "CREATED_AT",
                2,
                False,
                True,
                "NONCLUSTERED",
                "([DEL_FLAG]=(0))",
                False,
                2,
            ),
            (
                "IX_ORDER_ITEM_ACTIVE",
                False,
                False,
                "QTY",
                0,
                True,
                False,
                "NONCLUSTERED",
                "([DEL_FLAG]=(0))",
                False,
                3,
            ),
            (
                "PK_ORDER_ITEM",
                True,
                True,
                "ORDER_ITEM_ID",
                1,
                False,
                False,
                "CLUSTERED",
                None,
                False,
                1,
            ),
            (
                "UQ_ORDER_ITEM_ORDER_CODE",
                True,
                False,
                "ORDER_ID",
                1,
                False,
                False,
                "NONCLUSTERED",
                None,
                True,
                1,
            ),
        ],
    ),
    " as feature": (["feature", "detail"], [("data_compression", "PK_ORDER_ITEM:PAGE")]),
    "minor_id = 0": (["description"], [("รายการสินค้าในคำสั่งซื้อ",)]),
}


class FixtureCursor:
    def __init__(self) -> None:
        self.description: list[tuple[str]] = []
        self.rows: list[tuple[Any, ...]] = []
        self.timeout = 0

    def execute(self, query: str, parameters: Any = ()) -> None:
        normalized = " ".join(query.lower().split())
        self.description, self.rows = [], []
        for marker, (fields, rows) in FIXTURE.items():
            if marker in normalized:
                self.description = [(name,) for name in fields]
                self.rows = list(rows)
                return

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchmany(self, size: int = 1) -> list[tuple[Any, ...]]:
        result, self.rows = self.rows[:size], self.rows[size:]
        return result

    def close(self) -> None:
        return None

    def cancel(self) -> None:
        return None


class FixtureConnection:
    def cursor(self) -> FixtureCursor:
        return FixtureCursor()

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


PROFILE = ResolvedConnectionProfile(
    name="demo",
    engine=DatabaseEngine.SQLSERVER,
    host="localhost",
    port=1433,
    database="demo",
    username="user",
    password="password",
    allowed_schemas=("dbo",),
    allowed_object_types=(ObjectType.TABLE,),
)
REF = ObjectRef(
    object_id="table:dbo.ORDER_ITEM",
    engine=DatabaseEngine.SQLSERVER,
    schema_name="dbo",
    object_name="ORDER_ITEM",
    object_type=ObjectType.TABLE,
)


def extract() -> Any:
    adapter = SqlServerAdapter(lambda _: FixtureConnection())
    adapter._catalog[REF.object_id] = REF
    return adapter.extract_object(PROFILE, REF)


EXPECTED_DDL = """\
-- not rendered: data_compression (PK_ORDER_ITEM:PAGE)

CREATE TABLE [dbo].[ORDER_ITEM] (
    [ORDER_ITEM_ID] bigint IDENTITY(1,1) NOT NULL,
    [ORDER_ID] int NOT NULL,
    [ITEM_CODE] nvarchar(50) COLLATE Thai_CI_AS NOT NULL,
    [NOTE] nvarchar(max) NULL,
    [QTY] numeric(38,0) NOT NULL CONSTRAINT [DF__ORDER_ITE__QTY__1A2B3C] DEFAULT ((0)),
    [UNIT_PRICE] decimal(18,2) NOT NULL CONSTRAINT [DF_ORDER_ITEM_UNIT_PRICE] DEFAULT ((0.00)),
    [LINE_TOTAL] AS ([QTY]*[UNIT_PRICE]) PERSISTED,
    [CREATED_AT] datetime2(7) NOT NULL CONSTRAINT [DF_ORDER_ITEM_CREATED_AT] DEFAULT (sysdatetime()),
    [DEL_FLAG] bit NOT NULL CONSTRAINT [DF_ORDER_ITEM_DEL_FLAG] DEFAULT ((0)),
    CONSTRAINT [CK_ORDER_ITEM_QTY] CHECK ([QTY]>=(0)),
    CONSTRAINT [PK_ORDER_ITEM] PRIMARY KEY CLUSTERED ([ORDER_ITEM_ID] ASC),
    CONSTRAINT [UQ_ORDER_ITEM_ORDER_CODE] UNIQUE NONCLUSTERED ([ORDER_ID] ASC, [ITEM_CODE] DESC)
);

ALTER TABLE [dbo].[ORDER_ITEM] WITH CHECK ADD CONSTRAINT [FK_ORDER_ITEM_ORDER] FOREIGN KEY ([ORDER_ID]) REFERENCES [dbo].[ORDER_HEAD] ([ORDER_ID]) ON DELETE CASCADE;

ALTER TABLE [dbo].[ORDER_ITEM] WITH NOCHECK ADD CONSTRAINT [FK_ORDER_ITEM_ITEM] FOREIGN KEY ([ORDER_ID], [ITEM_CODE]) REFERENCES [dbo].[ORDER_LINE_REF] ([ORDER_ID], [ITEM_CODE]);

CREATE NONCLUSTERED INDEX [IX_ORDER_ITEM_ACTIVE] ON [dbo].[ORDER_ITEM] ([ORDER_ID] ASC, [CREATED_AT] DESC) INCLUDE ([QTY]) WHERE ([DEL_FLAG]=(0));

EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'รายการสินค้าในคำสั่งซื้อ', @level0type = N'SCHEMA', @level0name = N'dbo', @level1type = N'TABLE', @level1name = N'ORDER_ITEM';

EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'รหัสรายการสั่งซื้อ', @level0type = N'SCHEMA', @level0name = N'dbo', @level1type = N'TABLE', @level1name = N'ORDER_ITEM', @level2type = N'COLUMN', @level2name = N'ORDER_ITEM_ID';

EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'รหัสคำสั่งซื้อ', @level0type = N'SCHEMA', @level0name = N'dbo', @level1type = N'TABLE', @level1name = N'ORDER_ITEM', @level2type = N'COLUMN', @level2name = N'ORDER_ID';

EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'รหัสสินค้า', @level0type = N'SCHEMA', @level0name = N'dbo', @level1type = N'TABLE', @level1name = N'ORDER_ITEM', @level2type = N'COLUMN', @level2name = N'ITEM_CODE';

EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'จำนวน', @level0type = N'SCHEMA', @level0name = N'dbo', @level1type = N'TABLE', @level1name = N'ORDER_ITEM', @level2type = N'COLUMN', @level2name = N'QTY';

EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'ยอดรวม', @level0type = N'SCHEMA', @level0name = N'dbo', @level1type = N'TABLE', @level1name = N'ORDER_ITEM', @level2type = N'COLUMN', @level2name = N'LINE_TOTAL';"""


def test_fixture_ddl_is_lossless_and_neutral() -> None:
    assert extract().sanitized_definition == EXPECTED_DDL


def test_ddl_is_deterministic() -> None:
    assert extract().sanitized_definition == extract().sanitized_definition


def test_ddl_carries_no_product_specific_layout() -> None:
    ddl = extract().sanitized_definition
    for marker in (
        r"agrimap",
        r"agm",
        r"BEGIN\s+TRY",
        r"={4,}",
        r"IF\s+OBJECT_ID",
        r"-- Description",
    ):
        assert not re.search(marker, ddl, flags=re.IGNORECASE), marker


def test_structured_metadata_contains_every_item() -> None:
    obj = extract()
    columns = {column.name: column for column in obj.columns}
    assert [column.name for column in obj.columns][:3] == ["ORDER_ITEM_ID", "ORDER_ID", "ITEM_CODE"]
    assert columns["ITEM_CODE"].type_sql == "nvarchar(50)"
    assert columns["ITEM_CODE"].max_length == 100
    assert columns["ITEM_CODE"].collation == "Thai_CI_AS"
    assert columns["NOTE"].type_sql == "nvarchar(max)"
    assert columns["QTY"].type_sql == "numeric(38,0)"
    assert columns["CREATED_AT"].type_sql == "datetime2(7)"
    assert columns["ORDER_ITEM_ID"].identity is not None
    assert columns["ORDER_ITEM_ID"].identity.model_dump() == {"seed": "1", "increment": "1"}
    assert columns["LINE_TOTAL"].computed is not None
    assert columns["LINE_TOTAL"].computed.persisted is True
    assert columns["QTY"].default is not None
    assert columns["QTY"].default.system_named is True
    assert columns["QTY"].default.name == "DF__ORDER_ITE__QTY__1A2B3C"
    assert columns["ORDER_ID"].description == "รหัสคำสั่งซื้อ"
    assert obj.native_comment == "รายการสินค้าในคำสั่งซื้อ"

    constraints = {item.name: item for item in obj.constraints}
    assert constraints["PK_ORDER_ITEM"].clustered is True
    assert constraints["PK_ORDER_ITEM"].column_orders == ["ASC"]
    assert constraints["UQ_ORDER_ITEM_ORDER_CODE"].columns == ["ORDER_ID", "ITEM_CODE"]
    assert constraints["UQ_ORDER_ITEM_ORDER_CODE"].column_orders == ["ASC", "DESC"]
    assert constraints["CK_ORDER_ITEM_QTY"].expression == "([QTY]>=(0))"

    foreign_keys = {item.name: item for item in obj.foreign_keys}
    assert len(obj.foreign_keys) == 2
    assert foreign_keys["FK_ORDER_ITEM_ORDER"].on_delete == "CASCADE"
    assert foreign_keys["FK_ORDER_ITEM_ITEM"].source_columns == ["ORDER_ID", "ITEM_CODE"]
    assert foreign_keys["FK_ORDER_ITEM_ITEM"].trusted is False

    indexes = {item.name: item for item in obj.indexes}
    active = indexes["IX_ORDER_ITEM_ACTIVE"]
    assert active.key_columns == ["ORDER_ID", "CREATED_AT"]
    assert active.key_orders == ["ASC", "DESC"]
    assert active.included_columns == ["QTY"]
    assert active.filter_definition == "([DEL_FLAG]=(0))"
    assert active.clustered is False
    assert indexes["UQ_ORDER_ITEM_ORDER_CODE"].unique_constraint is True

    assert [item.model_dump() for item in obj.unsupported_features] == [
        {"feature": "data_compression", "detail": "PK_ORDER_ITEM:PAGE", "rendered": False}
    ]


def test_type_rendering_covers_lengths_and_alias_types() -> None:
    assert type_sql("varbinary", -1, 0, 0) == "varbinary(max)"
    assert type_sql("char", 10, 0, 0) == "char(10)"
    assert type_sql("nchar", 20, 0, 0) == "nchar(10)"
    assert type_sql("time", 5, 16, 3) == "time(3)"
    assert type_sql("float", 4, 24, 0) == "float(24)"
    assert type_sql("float", 8, 53, 0) == "float"
    assert type_sql("int", 4, 10, 0) == "int"
    assert (
        type_sql(
            "PhoneNo", 20, 0, 0, user_defined=True, type_schema="dbo", quote=lambda v: f"[{v}]"
        )
        == "[dbo].[PhoneNo]"
    )


def test_quotes_in_descriptions_are_escaped() -> None:
    from sqlctx.adapters.sqlserver import tables

    columns = tables.map_columns(
        [
            {
                "column_name": "A",
                "data_type": "int",
                "is_nullable": True,
                "ordinal_position": 1,
                "column_description": "it's",
            }
        ],
        lambda v: f"[{v}]",
    )
    ddl = tables.render_table(
        REF,
        columns=columns,
        constraints=[],
        foreign_keys=[],
        indexes=[],
        comment=None,
        features=[],
        quote=lambda v: f"[{v}]",
    )
    assert "@value = N'it''s'" in ddl
