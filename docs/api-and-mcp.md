# HTTP and MCP

The generated contracts are the source of truth:

- [OpenAPI](generated/openapi.json) — 42 authenticated operations across 38 paths
- [Core MCP](generated/mcp-tools.json) — 8 tools and 2 export resources
- [Session bridge](generated/mcp-bridge-tools.json) — 4 profile-session tools

Regenerate them with `scripts/generate_contract_schemas.py`; never hand-edit them.

Every request model rejects unknown fields. Every list that can grow carries `limit` and
`next_cursor` — a first page is not a result set, so keep paging until `next_cursor` is null.

Large SQL bodies, ZIP payloads, credentials and absolute filesystem paths never appear in an MCP
response.

## Transport

The bridge speaks stdio to the host and authenticated HTTP to the one shared service on
`127.0.0.1`. Bearer transport is restricted to validated IPv4 loopback endpoints, with proxy
environment variables and redirects disabled.

Do not point a host directly at the HTTP service. The bridge is what gives each session its own
active profile; bypassing it makes every window share one.

## Core MCP tools

| Group | Tools |
|---|---|
| Status and connection | `sqlctx_get_status`, `sqlctx_test_profile` |
| One-call pull | `sqlctx_pull`, `sqlctx_wait_pull` |
| Query | `sqlctx_query_data` |
| Context index | `sqlctx_list_context_index` |
| Routine deployment | `sqlctx_plan_routine_deployment`, `sqlctx_apply_routine_deployment` |

Since 4.0.0 the catalog, export, folder classification, index resolution and SQLFluff ensure/update
operations are owner CLI commands. Their HTTP routes remain for the CLI; see
[the 4.0.0 notes](releases/4.0.0.md) for the tool-by-tool replacement.

## Bridge tools

`sqlctx_get_active_profile`, `sqlctx_connect_profile`, `sqlctx_change_profile` and
`sqlctx_disconnect_profile`.

The active profile is session-local state held by the bridge. Connecting in one session never
affects another.

## Selected HTTP operations

```text
GET  /api/v1/managed-folders
POST /api/v1/managed-folder-plans
POST /api/v1/managed-folder-plans/{plan_id}/apply
POST /api/v1/context-index/search
POST /api/v1/context-index/sync
POST /api/v1/context-index/resolve
POST /api/v1/context-index/generation-plans
POST /api/v1/routine-plans
POST /api/v1/routine-plans/{plan_id}/apply
```

Folder registration stays owner-CLI only because it accepts absolute paths.

## Write semantics

`POST /api/v1/context-index/resolve` and `sqlctx_resolve_context_index` take a folder ID and a
managed relative path and return an immutable `FolderClassificationPlan`. Neither writes to the
database. Apply the plan through the managed-folder operation first, then sync those headers.

`sqlctx_sync_context_index` takes a `mode`:

| Mode | Writes | Approval | Can deactivate |
|---|---|---|---|
| `inventory` (default) | machine-derived identity and technical fields | no | no |
| `owner` | owner-confirmed classifications, deactivation | yes | with `complete_catalog_id` |

Inventory mode cannot overwrite an `OWNER` classification — the adapter's MERGE refuses — which
is what makes it safe to run unattended on every export.

Owner-mode writes and routine applies require the profile write scope **and** a single-use,
request-bound approval, so a first agent call may return `APPROVAL_REQUIRED`. Retry the identical
payload after the owner grants it; changing the plan ID, payload, caller or hashes will not
consume the grant.

One sync call accepts at most 5,000 entries. Larger catalogs are submitted as several inventory
calls with distinct idempotency keys — never truncated to fit.
