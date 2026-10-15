# Output format 2

New exports use output format `2`. The differences from v1: every managed SQL file carries a
context header, and all-mode materializes unresolved objects under `unknowns/`.

Existing v1 bundles still read and validate through the existing compatibility path, but the
writer never produces v1.

## Layout

```text
<context>/
  tables/
  table_metadata/
  samples/
  store_procedures/
  functions/
unknowns/
  tables/
  table_metadata/
  samples/
  store_procedures/
  functions/
indexes/
manifest.yaml
report.json
```

Confirmed objects use their `<context>` — for example `um`, `content`, `app_state`, `dd`.
Unresolved objects always have `context=null`, `tags=[]`, source `unknown`, and a path beginning
`unknowns/`.

A file in `unknowns/` is a successful export whose context nobody has confirmed yet. It is not a
failure, and it must never be moved by guessing from its filename.

## Managed SQL header

The first line is a single-line JSON comment, parsed strictly:

```sql
-- sqlctx-context: {"classification_source":"owner","classification_status":"confirmed","content_hash":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","context":"app_state","description":"Application state","engine":"sqlserver","evidence":[],"header_version":1,"object_id":"procedure:dbo.P","object_name":"P","object_type":"procedure","output_format_version":"2","schema_name":"dbo","source_fingerprint":null,"tags":["app_state","share"]}
CREATE OR ALTER PROCEDURE [dbo].[P] AS SELECT 1;
```

Rules:

- An unknown field, malformed JSON, unsafe context or tag, duplicate or unsorted tags, or an
  identity mismatch must fail.
- `content_hash` is the SHA-256 of the normalized, formatted SQL body with the header line
  removed.
- Reclassification changes only the managed header and path. It never does a global replace on a
  routine body.
- A SQL Server procedure body must carry the executable declaration
  `CREATE OR ALTER PROCEDURE`; a deploy-ready function uses `CREATE OR ALTER FUNCTION`.
- A banner comment (`--` or `/* */`) above the declaration is valid T-SQL. It is preserved
  verbatim and does not fail extraction. A comment is not an executable declaration, and a
  keyword found only inside a comment or string is never treated as one.

Never hand-edit a header or a hash. Regenerate the plan instead.

## Table metadata

Each table is written twice, with the same facts in both files:

- `tables/<table>.sql` is standard T-SQL that recreates the table. It has no product-specific
  layout, banners or wrappers. For SQL Server it contains `CREATE TABLE` in physical column
  order with inline identity, collation, defaults, computed columns, primary key, unique and
  trusted check constraints, then `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` (with
  `WITH CHECK`/`WITH NOCHECK`, ON DELETE/UPDATE, and `NOCHECK CONSTRAINT` for a disabled key),
  `CREATE INDEX` with key order, `INCLUDE` and `WHERE`, and `sys.sp_addextendedproperty` for
  the table and column `MS_Description`. A feature the DDL cannot express is listed at the top
  as `-- not rendered: <feature> (<detail>)`.
- `table_metadata/<schema>__<table>.yaml` is the machine-readable form a consumer formats from.

Fields added in 3.1.0 are optional. Engines other than SQL Server leave them `null`, `false`
or empty.

| Path | Field | Meaning |
|---|---|---|
| `columns[]` | `name`, `data_type`, `nullable`, `ordinal`, `sensitivity` | As before; `data_type` is the bare type name |
| `columns[]` | `type_sql` | Declared type: `nvarchar(50)`, `nvarchar(max)`, `numeric(38,0)`, `datetime2(7)`, `[schema].[alias]` |
| `columns[]` | `max_length`, `precision`, `scale` | Raw catalog values (`max_length` in bytes, `-1` for `max`) |
| `columns[]` | `collation` | Only when it differs from the database default |
| `columns[]` | `identity` | `{seed, increment}` as strings, or `null` |
| `columns[]` | `computed` | `{definition, persisted}`, or `null` |
| `columns[]` | `default` | `{name, definition, system_named}`, or `null`; the real name is kept |
| `columns[]` | `sparse` | `SPARSE` column |
| `columns[]` | `description` | `MS_Description`, Unicode preserved, personal data redacted |
| `constraints[]` | `name`, `constraint_type`, `columns`, `expression` | As before; `columns` in key ordinal order |
| `constraints[]` | `clustered`, `column_orders` | Primary key / unique: clustered flag and `ASC`/`DESC` per column |
| `constraints[]` | `enabled`, `trusted`, `system_named` | Check constraints may be disabled or `WITH NOCHECK` |
| `foreign_keys[]` | `source_columns`, `target_columns` | One entry per constraint, columns in constraint order |
| `foreign_keys[]` | `on_delete`, `on_update` | `NO_ACTION`, `CASCADE`, `SET_NULL`, `SET_DEFAULT` |
| `foreign_keys[]` | `enabled`, `trusted` | Disabled and `WITH NOCHECK` keys |
| `indexes[]` | `name`, `unique`, `primary`, `key_columns`, `included_columns` | As before |
| `indexes[]` | `clustered`, `index_type`, `key_orders`, `filter_definition` | Storage kind, `ASC`/`DESC` per key, filtered-index `WHERE` |
| `indexes[]` | `unique_constraint` | The index backs a unique constraint (rendered as the constraint, not as an index) |
| `unsupported_features[]` | `feature`, `detail`, `rendered` | `partitioned`, `filegroup`, `columnstore_index`, `xml_or_spatial_index`, `hash_index`, `data_compression`, `temporal`, `memory_optimized`, `filestream`, `sparse_column` (rendered), `column_set` |

Output is deterministic: the same database produces the same bytes.

## Accounting

These are all distinct numbers, and they are reported separately:

| Count | Meaning |
|---|---|
| `discovered` | Objects found inside the profile boundary |
| `fully_analyzed` | Objects whose definition and metadata were extracted |
| `analysis_failed` | Objects that could not be extracted |
| `materialized` | Objects written to the output |
| `intentionally_excluded` | Objects excluded by profile configuration |
| `security_skipped` | Objects withheld because a residual secret was detected |
| `unresolved` | Objects exported without a confirmed context |

All mode means every definition successfully extracted within the profile. It does not mean
every table row, and it never claims an object whose extraction failed.

`analysis_failed` is not a bare number. `sqlctx_get_catalog_status` and
`GET /api/v1/catalogs/{catalog_id}` return `failures[]` naming `object_id`, `object_type`,
`stage` (`analysis`, `sample` or `dependencies`), `error_code` and a sanitized `message`. The
export manifest records the same list at `export.analysis_failures[]`.

That `message` carries no credentials, no raw SQL body and no owner absolute path, and is
length-bounded. A failed object is never hidden or auto-excluded to make the numbers look
better.

## Manifest

The manifest and inventory record relative paths, byte sizes and hashes. Assembly may only
modify or delete files the existing manifest lists as managed; unmanaged files are preserved.
