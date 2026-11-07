---
name: sql-context-pack
description: Pull complete sanitized TABLE/PROCEDURE/FUNCTION context into an owner-registered folder in one call, query protected data and DB_METADATA_CONTEXT, and plan approval-gated SQL Server routine updates through the managed sqlctx service without exposing credentials or guessing context.
metadata:
  version: "4.0.0"
---

# SQL Context Pack

Use the managed loopback service. Never request database credentials, arbitrary absolute paths, raw
unmasked data, owner approval credentials, or unrestricted SQL execution.

## Route the request

| Owner asks for | Do |
|---|---|
| `doctor`, `doctor status/version/check/update/fix` | Follow [references/doctor.md](references/doctor.md). Default/status/version/check are read-only; report each finding's diagnosis and `proposed_fix.command`; only an owner-typed `fix` or `update` changes anything. |
| `help`, `guide` | Summarize pull, query, context index and routine deployment; link [`docs/getting-started.md`](../../docs/getting-started.md). |
| `profiles`, `connect`, `change-profile`, `disconnect` | Use the four bridge session tools. Never inherit another room's active profile. |
| What can I use? | `sqlctx_get_status`: capabilities, profiles, registered folder IDs, SQLFluff readiness. |
| Export or pull context into a folder, all or named objects | [Pull](#pull). |
| `query` | `sqlctx_query_data`: relational SELECT only, protected output, at most 500 rows over MCP. Follow [Sensitive data](#sensitive-data). |
| Context index lookup | `sqlctx_list_context_index` (paginated; read every page). |
| Routine update | [Routine deployment](#routine-deployment). |
| Folder classification, owner context resolution, generation planning, bundle fetch/assemble, SQLFluff ensure/update, profile scope/write-scope, folder registration, approvals, lifecycle | Owner CLI only. Give the exact command from [Owner commands](#owner-commands); do not emulate it. |

Recognize `$sql-content-pack` only as a typo for `$sql-context-pack`; keep the canonical name.

## Pull

`sqlctx_pull` runs discovery, analysis, export, SQLFluff, assembly into the registered folder,
reread validation and the inventory index sync as one service job.

1. Get the folder ID from `sqlctx_get_status`. With none registered, give the owner
   `sqlctx folder register --input-root <sql-input> --output-root <context-output> --engine <engine>`
   and stop; never accept an agent-supplied absolute path.
2. Call `sqlctx_pull` once. “All”, “ทั้งหมด” or equivalent with no name or pattern attached means
   no `include_patterns`. Named objects follow [Filtered scope](#filtered-scope). Pass the owner's
   numeric ID as `actor_id` when known (an all-digit ID such as `006006` is it; ask at most once
   per session) so the index is synced.
3. Status `blocked` lists every blocker with its remedy and started nothing: report the whole list
   once, exactly, and stop. Do not retry or ask for inputs one by one.
4. Otherwise call `sqlctx_wait_pull` with the `pull_id` until the status is `completed`,
   `completed_with_warnings` or `failed`. Each wait returns within 50 seconds; keep waiting while
   phase or progress moves.
5. Report the final `counts` separately (discovered, analyzed, analysis-failed, materialized,
   formatted, parse-failed, format-failed, security-skipped, files written, stale files kept, index
   submitted/inserted/updated/unchanged/owner-preserved), every warning with its remedy, and
   `failure_count` with the `failures` list, saying when `failures_truncated` is true. A warning is
   not a failure. Never claim a failed object was captured.
6. Then give one consolidated supervised list of unresolved objects (files under `unknowns/`), each
   with a preliminary suggested context and its evidence, marked as a suggestion. Never promote a
   suggestion yourself; owner answers go through `sqlctx context-index resolve`.

Unresolved objects are still exported under `unknowns/` with no guessed context. The index is SQL
Server only; on other engines the files are still complete. Stale files from earlier pulls are kept
and counted, never deleted by the agent.

## Filtered scope

When the owner names objects or name patterns, “all/ทั้งหมด” applies to the matches, not to the
database.

| Owner says | `include_patterns` / `exclude_patterns` |
|---|---|
| exact name `SYSTEM_CONFIG` | `["SYSTEM_CONFIG"]` |
| prefix / ขึ้นต้นด้วย `V2_` | `["V2_*"]` |
| suffix / ลงท้ายด้วย `_LOG` | `["*_LOG"]` |
| contains / มีคำว่า `CONFIG` | `["*CONFIG*"]` |
| except / ยกเว้น `LM_TEMP_*` | `exclude_patterns: ["LM_TEMP_*"]` |

Matching ignores letter case. Split by type when the owner does: “table V2_/LM_ and SP LM_” is one
pull with `object_types: ["table"]` and one with `["procedure"]`. An exact name that matches nothing
stops the request as a probable typo. Ask once only when a term is genuinely ambiguous (schema vs
prefix vs category, e.g. `ETL`). An explicit owner selection is enough to proceed.

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

## Routine deployment

1. Require a connected SQL Server profile and a registered folder ID.
2. Plan with `sqlctx_plan_routine_deployment`; omit `relative_path` only when the owner wants all
   eligible managed routines. The plan must hold exactly one Procedure/Function per file with
   matching header/body identity.
3. Apply with `sqlctx_apply_routine_deployment`; it needs `routine_write`, the same
   caller/profile/plan hashes and owner approval. Present the returned approval command, then retry
   the identical apply. Never change the plan or use DROP/recreate.
4. Other engines return `ROUTINE_APPLY_ENGINE_UNSUPPORTED`; report that boundary honestly.

## Owner commands

| Task | Command |
|---|---|
| Register a folder | `sqlctx folder register --input-root <in> --output-root <out> --engine <engine>` |
| Classify a folder | `sqlctx folder plan --folder-id <id>` then `sqlctx folder apply --plan-id <id>` |
| Confirm an unresolved object's context | `sqlctx context-index resolve --folder-id <id> --file <relative-path> --context <context>`, apply the plan it prints, then `sqlctx context-index sync-plan --profile <p> --actor-id <id> --idempotency-key <key> --plan-id <id>` |
| Plan generation from the index | `sqlctx context-index generate-plan --profile <p> --folder-id <id>` |
| Profile scope / write scope | `sqlctx profile scope <p> --schema <s>` · `sqlctx profile write-scope --profile <p> --metadata-context-write` |
| SQLFluff | `sqlctx sqlfluff status` · `sqlctx sqlfluff ensure` |
| Approve a challenge | `sqlctx approvals grant --challenge <id>` |
| Remove stale managed files | `sqlctx export fetch --export-id <id> --destination <file>` then `sqlctx export assemble --bundle <file> --output-root <out> --allow-delete-stale` |

## Preconditions and safety

1. Treat every bound as a fact to report, not a limit to hide. Read every page until
   `next_cursor` is null. A `truncated` query result is not an answer to "how many" or "all of
   them": say it was truncated and which bound was hit (`row_limit` or `output_limit`), then narrow
   the query or use owner CLI all-row output. Never present a bounded sample as the complete set.
2. Owner approval is single-use and request-bound. Present the returned command; never auto-grant.
3. Never expose bearer tokens, credentials, raw samples, SQL bodies in large MCP payloads, or owner
   absolute paths.
4. Never create a Python environment or project-local staging/cache directory.
5. Before asking the owner for any input, check every blocker you can see for the whole request.
   If one blocks, report it once with its exact remedy. Never ask for the same input twice.
6. A non-security limit (a bound, a skipped object, a failed format) is reported with its count
   while the rest of the request continues. Stop only for a security boundary, a missing approval,
   or a contract error the service returned.

Use [references/contracts.md](references/contracts.md) for tool contracts and error boundaries.
