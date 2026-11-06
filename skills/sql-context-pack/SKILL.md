---
name: sql-context-pack
description: Build complete sanitized TABLE/PROCEDURE/FUNCTION context, classify owner-registered SQL folders, query DB_METADATA_CONTEXT, and plan approval-gated SQL Server routine updates through the managed sqlctx service without exposing credentials or guessing context.
metadata:
  version: "3.5.4"
---

# SQL Context Pack

Use the managed loopback service. Never request database credentials, arbitrary absolute paths, raw
unmasked data, owner approval credentials, or unrestricted SQL execution.

## Route the request

- `doctor`, `doctor status/version/check/update/fix`: use [references/doctor.md](references/doctor.md).
  Default/status/version/check are read-only. Report each finding's diagnosis and propose its
  `proposed_fix.command`; only an owner-typed `fix` or `update` changes anything; retain `doctor --mcp`.

- `help` or `guide`: summarize complete export, Query Data, folder classification, context index,
  generation planning, and routine deployment; link repository users to
  [`docs/getting-started.md`](../../docs/getting-started.md).
- `profiles`, `connect`, `change-profile`, `disconnect`: use the four bridge session tools. Never
  inherit another room's active profile.
- complete context: use catalog/export workflow from [references/workflow.md](references/workflow.md).
- named objects or name patterns: follow [Filtered scope](#filtered-scope). An explicit owner
  selection is enough to proceed; do not ask again or stop over it.
- folder classification: list registered folder IDs, plan first, and apply only the unchanged plan.
- context index: list freely. Every export ends with an `inventory` sync of every discovered
  object — no approval, never overwrites an owner value. Owner resolution first creates a
  managed-file plan, then applies that plan before an `owner`-mode sync, which is approval-gated.
  All syncs need a numeric actor ID, profile write scope and an idempotency key. An all-digit
  owner ID the owner gives (for example `006006`) is the actor ID; ask for it at most once per
  session and reuse it.
- generation: call `sqlctx_plan_context_generation`; treat drift as a stop, not a warning.
- routine update: plan one registered relative file or the whole folder, then approval-gated apply.
- `query`: use `sqlctx_query_data`; relational SELECT only, protected output, max 500 rows over
  MCP. Follow [Sensitive data](#sensitive-data) for every result.
- `format`, profile configuration/removal/write-scope, folder registration, approvals, lifecycle
  and uninstall are owner CLI operations; give the exact command instead of emulating them.
- fetch/assemble/validate: run `sqlctx export fetch`, `sqlctx export assemble` and
  `sqlctx validate output` yourself when the owner's request names a destination inside the open
  workspace (for example `.agrimap-agent/`). With no named destination, or one outside the
  workspace, give the exact commands instead.

Recognize `$sql-content-pack` only as a typo for `$sql-context-pack`; keep the canonical name.

## Complete capture

Tables, stored procedures and stored functions are exportable. A profile created before function
support keeps its stored allowlist; tell the owner to update profile scope when `FUNCTION` is absent.
“All”, “ทั้งหมด”, or equivalent **with no name or pattern attached** means `selection.mode=all`
with empty `include_patterns`. It analyzes every permitted object. Profile
schemas/types/exclusions remain authoritative.

## Filtered scope

When the owner names objects or name patterns, “all/ทั้งหมด” applies to the matches, not to the
database. Send one catalog per object type set with `include_patterns` and
`selection.mode=filtered`; never `all` with patterns (`ALL_MODE_INCLUDE_FILTER_CONFLICT`).

| Owner says | `include_patterns` / `exclude_patterns` |
|---|---|
| exact name `SYSTEM_CONFIG` | `["SYSTEM_CONFIG"]` |
| prefix / ขึ้นต้นด้วย `V2_` | `["V2_*"]` |
| suffix / ลงท้ายด้วย `_LOG` | `["*_LOG"]` |
| contains / มีคำว่า `CONFIG` | `["*CONFIG*"]` |
| except / ยกเว้น `LM_TEMP_*` | `exclude_patterns: ["LM_TEMP_*"]` |

Matching ignores letter case. Split by type when the owner does: “table V2_/LM_ and SP LM_” is
one `table` catalog with `["V2_*","LM_*",...]` and one `procedure` catalog with `["LM_*"]`. An
exact name that matches nothing stops the request as a probable typo; a wildcard that matches
nothing is returned in `unmatched_wildcard_patterns` and reported as zero matches. Ask once only
when a term is genuinely ambiguous (schema vs prefix vs category, e.g. `ETL`). A filtered catalog
never proves complete scope and never feeds `complete_catalog_id`.

When the owner also asks to replace existing project SQL files, delete them only after
`sqlctx validate output` passes for the new bundle, and list what was removed.

Unresolved classification does not block all-mode export. Materialize it under
`unknowns/tables|store_procedures|functions` with no guessed context/tags. Every managed SQL file
must have the v2 header. SQL Server procedures must retain exact `CREATE OR ALTER PROCEDURE` after
the header.

TABLE capture is DDL/metadata plus bounded protected, marked samples, never every table row. Do not claim a
failed extraction was captured; report discovered, analyzed, failed, materialized, excluded,
security-skipped, and unresolved counts separately.

## Sensitive data

The service protects personal and secret values in code before any result reaches you. You
never see real sensitive values, and you must never try to.

1. Every protected column is marked in the result header and in `columns[].marker`:
   `⟨FAKE:…⟩` (names, Thai ID, phone, account: realistic but fake), `⟨ALIAS:…⟩` (email,
   username), `⟨GENERALIZED:…⟩` (address, birth year, coarse location), `⟨REDACTED:…⟩`
   (secrets, unknown lineage) and `⟨SCANNED…⟩` (free text with embedded PII replaced).
2. Whenever you quote a marked value, say it is fake, aliased, generalized or redacted. Never
   present a fake as a real person, ID or number.
3. Protected columns may only be selected directly. Filtering, joining, grouping, ordering,
   CASE or functions on them return `QUERY_SENSITIVE_USAGE_RESTRICTED`; do not rewrite the
   query to get around it.
4. When the user needs real values, or a result or error carries `reveal_handoff`, give the
   user its `sql` and `user_steps` verbatim so they run it themselves, outside this session.
   Never run `sqlctx query --reveal` yourself; it refuses non-interactive terminals by design.
5. Export samples, routine-body string literals and table descriptions are protected the same
   way; `[REDACTED:<CLASS>]` in exported SQL marks a removed literal, not a real value.

## Registered folder and index workflow

1. If no folder ID exists, tell the owner to run `sqlctx folder register`; never accept an Agent-
   supplied absolute root.
2. Plan with `sqlctx_plan_folder_classification`. Suggestions stay suggestions. Only owner-supplied
   resolutions become confirmed metadata; everything else remains `unknowns`.
3. Apply to separate output by default. In-place apply is explicit and approval-bound.
4. To resolve an already-managed unknown, call `sqlctx_resolve_context_index` with its folder ID and
   relative path. Apply the returned immutable in-place folder plan before syncing the same plan;
   never write a DB-only owner resolution.
5. Sync `[agrimap_app].[DB_METADATA_CONTEXT]` on **every** export, in the default `inventory`
   mode, covering every discovered object. Objects you cannot classify go in as
   `classification_status=unresolved` with no guessed context — that is the correct record for
   them, and leaving them out is not. Inventory mode needs no approval and can neither
   overwrite an owner classification nor deactivate a row. If the profile has not enabled
   `metadata_context_write`, report `details.owner_command` and carry on; that is not an export
   failure and not a reason to skip the sync next run. On PostgreSQL, MySQL, MariaDB and Oracle
   the sync returns `METADATA_CONTEXT_ENGINE_UNSUPPORTED`: the index is SQL Server only, so state
   that boundary plainly and continue — those engines still export full context files.
6. Then give the owner one consolidated supervised list of every unresolved object with a
   preliminary suggested context and its evidence, marked as a suggestion. Never promote a
   suggestion to a confirmed context yourself — a quietly mis-assigned context is worse than an
   unresolved row. Owner answers go back through `owner` mode, which is approval-gated.
7. Report inserted, updated, unchanged, deactivated and owner-values-preserved counts separately,
   plus submitted count against discovered count. Supply `complete_catalog_id` only for an exact,
   unfiltered, zero-failure all-mode catalog inventory; only that proven mode may deactivate
   missing active rows, and it requires `owner` mode.
8. Listing is paginated. Generation selects by context/tag/type, excludes unresolved by default,
   and stops on index/header/body hash drift.

## Routine deployment workflow

1. Require a connected SQL Server profile and a registered folder ID.
2. Plan with `sqlctx_plan_routine_deployment`; omit `relative_path` only when the owner wants all
   eligible managed routines.
3. The plan must contain exactly one Procedure/Function per file and matching header/body identity.
4. Actual apply requires `routine_write`, the same caller/profile/plan hashes, and owner approval.
5. Retry the identical apply after approval. Never promote a changed plan or use DROP/recreate.
6. Other engines return `ROUTINE_APPLY_ENGINE_UNSUPPORTED`; report that boundary honestly.

## Preconditions and safety

1. Confirm service, capabilities, profiles, active profile and SQLFluff readiness.
2. Read every cursor until `next_cursor` is null. A first page is not a result set. Paged
   tools cap a single page, not the total, so keep paging rather than reporting the cap.
3. Treat every bound as a fact to report, not a limit to hide. A `truncated` query result is
   not an answer to "how many" or "all of them": say it was truncated, say which bound was
   hit (`row_limit` or `output_limit`), and either narrow the query or use owner CLI all-row
   output. Sync batches at 5,000 entries per call — send every batch, never trim the
   inventory to one call. Never present a bounded sample as the complete set.
4. Use stable non-secret idempotency keys and exact fingerprint matches for resume.
5. Owner approval is single-use and request-bound. Present the returned command; never auto-grant.
6. Keep ZIP transfer in `sqlctx export fetch`, assembly in `sqlctx export assemble`, and final local
   reread in `sqlctx validate output`.
7. Never expose bearer tokens, credentials, raw samples, SQL bodies in large MCP payloads, or owner
   absolute paths.
8. Never create a Python environment or project-local staging/cache directory.
9. Do not claim completion until inventory, hashes, accounting and server validation all pass.
10. Before asking the owner for any input (actor ID, destination, confirmation), check every
    blocker you can see for the whole request: write scope, other loaded instructions that
    forbid the step, service readiness. If one blocks, report it once with its exact remedy
    instead of collecting inputs you cannot use. Never ask for the same input twice.
11. A non-security limit (a bound, a skipped object, a pending format job) is reported with its
    count while the rest of the request continues. Stop only for a security boundary, a missing
    approval, or a contract error the service returned.

Use [references/contracts.md](references/contracts.md) for tool names, approvals, error boundaries and
completion equations.
