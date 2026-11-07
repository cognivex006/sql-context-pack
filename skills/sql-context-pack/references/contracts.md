# Operation contract

The service exposes eight core `sqlctx_*` tools, four session bridge tools, and exactly two small
export resources. Generated schemas are authoritative. ZIPs, credentials, unrestricted paths and
large SQL bodies are never MCP resources.

| Tool | Contract |
|---|---|
| `sqlctx_get_status` | Read-only, probes no database: capabilities, safe profile descriptors, opaque folder IDs (never roots), SQLFluff readiness. |
| `sqlctx_test_profile` | Bounded read-only connection test; the bridge calls it for `connect`/`change`. |
| `sqlctx_pull` | One job; see [Pull job](#pull-job). |
| `sqlctx_wait_pull` | Returns on any phase/progress change or after at most 50 s; `timeout_seconds=0` returns at once. |
| `sqlctx_query_data` | Relational SELECT only, protected, 1–500 rows; see [Query Data](#query-data). |
| `sqlctx_list_context_index` | Read-only, paginated; filters context, tag, type, status, active. |
| `sqlctx_plan_routine_deployment` | Binds profile, caller, engine, ordered files, hashes and expiry. |
| `sqlctx_apply_routine_deployment` | Requires `routine_write`, the exact plan binding and owner approval. |

## Pull job

- Needs a registered folder ID; profile defaults to the session's active profile. No include
  patterns means every permitted object; patterns mean filtered scope, which never proves complete
  profile scope.
- Include and exclude patterns ignore letter case. An unmatched exact name stops the job as a
  probable typo; an unmatched wildcard is reported as zero matches unless every pattern is
  unmatched.
- Preflight failures return status `blocked` with every blocker (`code`, `message`, `remedy`) and
  start nothing. Missing `actor_id`, missing `metadata_context_write` or a non-SQL Server engine
  only skip the index sync and appear as warnings.
- Unresolved objects are materialized under `unknowns/` with no guessed context and synced as
  `unresolved`; inventory mode never overwrites an owner classification or deactivates a row.
- Assembly writes only under the folder's output root, never overwrites unmanaged files
  (`UNMANAGED_FILE_CONFLICT`) and never deletes stale managed files; `stale_files_kept` counts them.
- Per-object failures are in `failures` (at most 200), with `failure_count` and
  `failures_truncated`.
- An identical pull already running for the caller is returned instead of started twice. A pull a
  service restart interrupted ends `failed` with `PULL_INTERRUPTED`.

## Query Data

Results carry `columns[].sensitivity|treatment|marker`, `protected_value_counts` and, when any
column is protected, `reveal_handoff` (user-run steps; `model_may_view` is always false). There is
no MCP `all_rows`. `QUERY_SENSITIVE_USAGE_RESTRICTED` (403) means a protected column was used outside
a plain projection; relay its `reveal_handoff`.

## Context index

- Every index operation fails closed on table signature drift across columns, types, nullability,
  identity, defaults, checks and indexes, unexpected constraints, indexes, triggers or foreign keys,
  and index key direction; the runtime never auto-migrates the table.
- Owner-confirmed context, description and tags are never overwritten by rule or unknown sync.
- Owner resolution, owner-mode sync and complete reconciliation are owner CLI operations
  (`sqlctx context-index resolve|sync-plan`); a DB-only resolution is forbidden.

## Routine deployment

- SQL Server Procedure/Function use reviewed CREATE OR ALTER normalization.
- Unsupported engines return `ROUTINE_APPLY_ENGINE_UNSUPPORTED`; never DROP/recreate.

## Approval

Privileged calls first return `APPROVAL_REQUIRED` with challenge ID, expiry and exact
`sqlctx approvals grant --challenge ID`. The owner grants locally; retry the identical operation.
Never ask for the owner credential or change the payload after grant.

## Completion

```text
discovered = analyzed + analysis_failed
materialized = formatted + parse_failed + format_failed
```

Stop on unsafe paths, credential-policy failure, weakened masking, hash/schema drift, invalid header,
unproven inventory or failed validation.
