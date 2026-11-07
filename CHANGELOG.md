# Changelog

Release history. For how to use the product, start with [Getting Started](docs/getting-started.md).

## 4.0.0 — 2026-11-07

Major: eight core MCP tools, phase 3 of the [4.0.0 roadmap](docs/proposals/4.0.0-roadmap.md).
Requirement [v2.0](docs/spec/design-spec-v2.0.md). Notes and the tool-by-tool migration table:
[docs/releases/4.0.0.md](docs/releases/4.0.0.md).

Why: with 37 tools and a twenty-step workflow, an agent had many ways to take a wrong turn: a
preview it did not page, a selection it set twice, a bundle it fetched without assembling. Since
3.6.0 one pull does the whole job, so the step-by-step tools only added ways to fail.

**Removed** (MCP only; the owner CLI and HTTP API keep every capability)

- Catalog: `sqlctx_list_catalogs`, `sqlctx_create_catalog`, `sqlctx_get_catalog_status`,
  `sqlctx_cancel_catalog`, `sqlctx_delete_catalog`, `sqlctx_get_category_preview`,
  `sqlctx_set_materialization_selection`, `sqlctx_list_sitemap`,
  `sqlctx_get_materialization_plan`, `sqlctx_get_classification_requests`,
  `sqlctx_submit_classification_proposals`, `sqlctx_resolve_classifications`.
- Export: `sqlctx_list_exports`, `sqlctx_export_batch`, `sqlctx_get_export_status`,
  `sqlctx_cancel_export`, `sqlctx_delete_export`, `sqlctx_validate_exports`.
- Discovery and tooling: `sqlctx_get_capabilities`, `sqlctx_list_profiles`,
  `sqlctx_list_managed_folders`, `sqlctx_sqlfluff_status`, `sqlctx_sqlfluff_ensure`,
  `sqlctx_sqlfluff_update`.
- Folders and index: `sqlctx_plan_folder_classification`, `sqlctx_apply_folder_classification`,
  `sqlctx_sync_context_index`, `sqlctx_resolve_context_index`, `sqlctx_plan_context_generation`.
- Pull: `sqlctx_get_pull_status` (use `sqlctx_wait_pull` with `timeout_seconds=0`).

**Added**

- `sqlctx_get_status` (and `GET /api/v1/status`): capabilities, safe profiles, registered folder
  IDs and SQLFluff readiness in one read-only call.
- The pull status lists per-object failures (`failures`, at most 200, with `failure_count` and
  `failures_truncated`), which the removed catalog tools used to show.

**Changed**

- `SKILL.md` is one routing table to the eight tools or an exact owner command. The twenty-step
  `references/workflow.md` is removed.

## 3.6.0 — 2026-11-07

Minor: one-call pull, phase 2 of the [4.0.0 roadmap](docs/proposals/4.0.0-roadmap.md). Requirement
[v1.9](docs/spec/design-spec-v1.9.md). Notes: [docs/releases/3.6.0.md](docs/releases/3.6.0.md).

Why: a complete export took the agent about twenty tool calls (catalog, polls, previews,
sitemaps, selection, export, polls, fetch, assemble, validate, index batches), and any step could
loop, ask a question the service could answer, or block one MCP call long enough to hit the
client's 30-minute idle timeout. `create_catalog` alone ran a whole analysis inside one request.

**Added**

- `sqlctx_pull` starts one service job that discovers, analyzes, exports, formats, assembles into
  a registered folder's output root, rereads the written files, and syncs the context index in
  inventory mode. No include patterns means every permitted object; patterns mean filtered scope.
- One preflight: profile, schema and type scope, folder registration, SQLFluff readiness and a
  bounded connection test are checked together. Any failure returns status `blocked` with every
  blocker and its exact owner command, and starts nothing.
- `sqlctx_wait_pull` returns on any change of phase or progress, or after at most 50 seconds;
  `sqlctx_get_pull_status` returns without waiting. HTTP: `POST /api/v1/pulls`,
  `GET /api/v1/pulls/{pull_id}`, `GET /api/v1/pulls/{pull_id}/wait`.
- One report: discovered, analyzed, failed, materialized, formatted, parse-failed, format-failed,
  security-skipped, files written, stale files kept and index counts, plus every warning.
- The service derives catalog, export and index idempotency keys, sends index batches of 5,000
  itself, and returns an identical pull already running instead of starting a second.
- `assemble_bundles(keep_stale=True)` keeps files an earlier run managed but this run did not
  produce, still managed, instead of refusing; the pull reports how many.

**Fixed**

- Creating a catalog through the service failed with an internal error on PostgreSQL, MySQL,
  MariaDB and Oracle: the base adapter's schema fingerprint read `.value` from object types that
  are stored as plain strings. SQL Server, which computes its own fingerprint, was unaffected.
  Found by the new end-to-end pull test.
- An unexpected failure inside a pull writes its traceback to the protected runtime `errors/`
  folder and puts only the correlation ID in the status.

**Changed**

- The Skill routes "export into a folder" requests to `sqlctx_pull` first. The step-by-step
  catalog and export workflow stays available and unchanged.

## 3.5.7 — 2026-11-07

Patch: phase 1b of the [4.0.0 roadmap](docs/proposals/4.0.0-roadmap.md). Notes:
[docs/releases/3.5.7.md](docs/releases/3.5.7.md).

Why: updating and checking a real Windows machine through 3.5.4 to 3.5.6 hit four faults that
each made a working installation look broken, or a working table look empty.

**Fixed**

- **`doctor` never saw a Claude Code plugin.** `claude plugin list --json` names plugins under
  `id`, which doctor did not match, so every Claude install reported `PLUGIN_VERSION_UNVERIFIED`.
  A plugin loaded from a local marketplace folder now reports the version it actually loads
  (`folderVersion`) rather than the older recorded cache entry.
- **`doctor update` stopped on machines set up through a host marketplace.** Those leave no
  install provenance, so update failed with `UPDATE_SOURCE_REQUIRED`. Update now fetches the
  latest release's one-line updater, runs it only if it matches the release `SHA256SUMS`, and
  verifies package, plugin and service versions. A `--version` other than the latest still
  stops, with the latest version in `details`, rather than installing something else.
- **`sqlctx service status` reported a healthy Windows service as `not_running`.** It read the
  health token from the per-user runtime folder; the SCM service keeps it under ProgramData.
- **Tables with `geometry`, `geography` or `hierarchyid` columns had no sample.** pyodbc cannot
  read ODBC type `-151`, so one such column failed the whole sample (8 tables in the test
  database). Those values now arrive as bytes and show the existing `[BINARY n BYTES]`
  placeholder.

New error codes `UPDATE_RELEASE_UNAVAILABLE` and `UPDATE_RELEASE_UNVERIFIED`. No tool, option,
field or managed header changes. Output format stays 2.

## 3.5.6 — 2026-11-06

Patch: keep formatting when only unfixable lint remains. Notes:
[docs/releases/3.5.6.md](docs/releases/3.5.6.md).

Why: a 1,032-object export of a SQL Server development database reported 924 objects as
`format_failed`, and those files were written unformatted. `sqlfluff format` writes every fix it
can make and then exits 1 when any violation it cannot fix remains. The formatter treated that
exit code as failure and kept the original text, so it discarded formatted SQL that SQLFluff had
already produced. Earlier exports from 3.x show the same ratio.

**Fixed**

- An exit 1 whose only cause is remaining unfixable lint keeps the formatted file. It still has
  to pass the verifying parse and keep the managed header, as every formatted file does, and
  the result carries the diagnostic `unfixable_lint_violations_remaining: <count>`.
- Parse or templating errors (whose fixes SQLFluff discards), any other exit code, and a file
  that no longer parses after formatting still preserve the original text, as before.
- The format cache policy moves to `parse-format-verify-v3`, so results cached as failed by
  3.5.5 are formatted again instead of being reused.

Re-running the 924 objects through the fixed formatter: all 924 formatted (850 changed, 74
already clean), every managed header intact. The 37 objects SQLFluff cannot parse are unchanged.

No tool, option, field or managed header changes. Output format stays 2; the formatted body of
an affected object differs from 3.5.5.

## 3.5.5 — 2026-11-06

Patch: Windows installer fixes. Notes: [docs/releases/3.5.5.md](docs/releases/3.5.5.md).

Why: updating a Windows machine to 3.5.4 failed twice before anything reached the service.

**Fixed**

- **Python not found under Windows PowerShell 5.1.** `scripts/python-preflight.ps1` passed
  Python a probe containing double quotes. Windows PowerShell 5.1 hands embedded double quotes
  to native programs unescaped, so Python received broken code and every interpreter looked
  unavailable (`PYTHON_UNAVAILABLE`, exit 3). The documented one-line command starts Windows
  PowerShell, so it failed on every Windows machine at this step. The probe now uses single
  quotes only and works under both 5.1 and 7.
- **Update stopped on folder permissions it already had.** Before elevating, the installer
  re-granted the service Modify access to every registered folder with `icacls /grant`. On a
  folder owned by `BUILTIN\Administrators` a non-elevated owner may not change permissions, so
  the update stopped with `SERVICE_FOLDER_ACCESS_FAILED` even though the grant was already in
  place. An existing inheritable Modify or Full grant is now recognized and left alone; a
  missing grant is applied as before, and a deny entry still forces it.

No tool, option, field, managed header or output byte changes. Output format stays 2.

## 3.5.4 — 2026-11-06

Patch: metadata read timeout. Notes: [docs/releases/3.5.4.md](docs/releases/3.5.4.md).

Why: a complete catalog of `agrimap_app` on a SQL Server development database stopped making
progress at object 666 of 1,032 and never recovered; the MCP call was abandoned after 30 minutes.
Query Data and writes set a statement timeout, but catalog, sample and dependency reads did not,
so one read blocked by a lock on a single object stalled the whole catalog indefinitely.

**Fixed**

- Every catalog, sample and dependency read sets a 120-second statement timeout where the driver
  supports one (SQL Server through pyodbc). A read that exceeds it fails that object with
  `DATABASE_OPERATION_FAILED`, counted in `analysis_failed_object_count` and listed in
  `failures[]`, while the rest of the catalog completes.
- A read that timed out is not retried on a fresh connection, so one blocked object costs one
  timeout, not two. Other failures on a reused connection are still retried once.

No tool, option, field, managed header or output byte changes. Output format stays 2.

## 3.5.3 — 2026-11-06

Patch: extraction and formatting performance, phase 1 of the 4.0.0 track. Requirement
[v1.8](docs/spec/design-spec-v1.8.md). Notes: [docs/releases/3.5.3.md](docs/releases/3.5.3.md).

Why: the owner reported that complete exports took far too long and were often retried. Three
costs dominated, all paid per object and in sequence. Every metadata statement opened and closed
its own database connection, so a table cost about nine handshakes and logins. Objects were
analyzed one at a time. And formatting one object launched a new Python interpreter three times
(`sqlfluff parse`, `format`, `parse`), importing SQLFluff each time.

**Changed**

- Metadata reads during fingerprinting, discovery and analysis reuse one connection per worker
  thread. Each statement still runs the read-only setup in its own transaction and is rolled
  back before the next. A statement that fails on a reused connection drops it and is retried
  once on a fresh one. Writes, routine deployment and Query Data never reuse a connection.
- Phase-two analysis reads definitions, samples, sensitivity classes and dependencies on at most
  four concurrent sessions per catalog. Masking, checkpoints, progress and accounting stay on one
  thread in catalog order, so snapshots and bundles are identical to a sequential run.
  Cancellation now reaches every running statement.
- SQLFluff runs in a small pool of long-lived workers that call the same command-line entry
  point with the same arguments. A worker that cannot start or answer falls back to one ordinary
  subprocess for that command, so output never depends on the worker. On the development
  machine ten objects took 13.6 s instead of 43.2 s, with identical status and content.
- The output writer formats up to the worker-pool size at once and writes files, progress and
  accounting in catalog order.

No tool, option, field, managed header or output byte changes. Output format stays 2.

## 3.5.2 — 2026-11-02

Patch: Antigravity install on Windows, and one-line setup. Notes:
[docs/releases/3.5.2.md](docs/releases/3.5.2.md).

Why: on Windows the checkout installer could not install the `agy` host. `install.ps1` accepted
`-Harness agy` but always asked `scripts/install-global.ps1` for the plugin layout, and that
script accepted only `codex`, `claude` and `gemini`, so parameter binding failed before anything
was installed. `sqlctx doctor update --host agy` runs that installer on Windows, so it failed too,
although the Antigravity guide gives it as the install command.

Setting up a machine also took a marketplace add, a plugin add, a Skill setup run in one session
and a profile connect in another, with a different command on each host. The owner asked for a
single install command and a single update command, always on the latest release, with the
loading display the DevSift installers use.

**Fixed**

- `install.ps1 -Harness agy` installs the bare Skill layout (`--mode skill`), the only one
  Antigravity loads, as `install.sh` already did. Other hosts still get the plugin layout.
- `scripts/install-global.ps1` accepts `-Harness agy`.
- Contract tests run both PowerShell installers in a sandbox and check that `agy` reaches
  `global_install.py ... --harness agy --mode skill`, and that `global_install.py` installs the
  Antigravity Skill and merges only its own `mcp_config.json` entry.

`scripts/lifecycle.ps1` still does not accept `agy`; Antigravity uninstall stays manual, as
[docs/providers/antigravity.md](docs/providers/antigravity.md) describes.

**Added**

Distribution only: the package, CLI, MCP surface and Skill contract are unchanged.

- One-line setup: `installer/install.ps1`, `update.ps1`, `install.sh` and `update.sh`, published
  as release assets. They resolve the latest tag, verify `sql-context-pack-install.zip` against
  its SHA-256, unpack it to a per-user release folder, run that release's own installer for each
  detected host, and check package, plugin and service versions with `sqlctx doctor check`.
  A two-row terminal display shows overall progress, download bytes, the current stage and its
  elapsed time. `SQLCTX_HOST` and `SQLCTX_DRY_RUN` override detection and changes.
- A host already registered from another marketplace keeps that registration; setup installs
  only the package and service for it, so the Skill is never listed twice.
- `scripts/build_release_assets.py` builds and uploads those assets from the release tag.

## 3.5.1 — 2026-10-31

Patch: metadata schema check fix. Notes: [docs/releases/3.5.1.md](docs/releases/3.5.1.md).

Why: on a SQL Server database where the packaged `DB_METADATA_CONTEXT` DDL had been applied
as written, inventory sync still failed with `METADATA_CONTEXT_SCHEMA_DRIFT` for five check
constraints. SQL Server stores a check in its own rendering, and the check compared text with
only brackets, parentheses and whitespace removed. The owner was sent to re-apply a schema that
was already correct.

**Fixed**

- Check constraints are compared as canonical expression trees. `IN` lists rendered as `OR`
  chains in any order, `BETWEEN` rendered as `>=`/`<=`, `NOT LIKE` rendered as `NOT … LIKE`,
  added parentheses and lower-case keywords or function names now match the contract.
- A changed literal, its letter case, operator, bound, collation, negation, function or
  precedence is still drift. The previous text comparison ignored parentheses, so a predicate
  regrouped with the same tokens passed; it is now drift. A definition outside the parsed subset
  falls back to the strict text comparison.
- Regression tests use the definitions SQL Server actually returned from `sys.check_constraints`,
  and cover `is_disabled`, `is_not_trusted` and `is_not_for_replication`.

## 3.5.0 — 2026-10-30

Requirement [v1.7](docs/spec/design-spec-v1.7.md). Notes: [docs/releases/3.5.0.md](docs/releases/3.5.0.md).

Why: an owner on Codex asked for every table named `V2_*`/`LM_*`, three named config tables and
every `LM_*` procedure, saved under `.agrimap-agent/`. The Skill refused twice: “all” was defined
only as the whole database, the service still accepted `all` on a filtered catalog so the agent
discarded its own export, and fetch/assemble was owner-only even with a named destination. A
security review in the same session also found master keys stored beside their ciphertext and
schema routes served without a bearer.

**Added**

- `filtered` selection mode with inclusion reason `filtered_scope`; requires include patterns
  (`FILTERED_MODE_INCLUDE_REQUIRED`) and never proves complete scope.
- `unmatched_wildcard_patterns` on catalog status: a wildcard that matches nothing is reported as
  zero instead of failing the request. Exact names that match nothing still fail.
- Skill “Filtered scope” rules mapping exact/prefix/suffix/contains/except to patterns; agent-run
  fetch/assemble/validate when the owner names a destination in the workspace; continue past
  non-security limits with counts.
- `CREDENTIAL_KEY_UNWRAPPED` (read-only) and `UPDATE_SIGNATURE_UNVERIFIED` findings.
- [Upgrade guide](docs/knowledge/upgrade-to-3.5.0.md) for agents upgrading any earlier install.

**Changed**

- `sqlctx_set_materialization_selection` rejects `all` on an include-pattern catalog.
- Exclude patterns ignore letter case, matching include patterns.

**Fixed**

- SQL Server context index read and sync failed with `DATABASE_OPERATION_FAILED`
  (`decimal.ConversionSyntax`): the metadata schema check read identity seed/increment as
  `sql_variant`, which pyodbc cannot convert. They are now cast to text, as the table exporter
  already did.
- Skill: an all-digit owner ID is the actor ID, asked at most once; blockers are checked before
  any input is requested, so a run no longer asks for an actor ID and then refuses.

**Security**

- Windows master keys are protected with machine-scope DPAPI; legacy plain keys convert in place
  on next read with the same key bytes, so no credential is re-entered.
- `/api/docs`, `/api/openapi.json` and ReDoc are no longer served.
- `doctor update` reports whether the pinned release tag has a verifiable signature.
- Not in this release: hash-pinned dependency installation (needs a cross-platform lock file) and
  enforced tag signatures (needs a release signing key). Both remain open.

## 3.4.0 — 2026-10-29

Requirement [v1.6](docs/spec/design-spec-v1.6.md). Notes: [docs/releases/3.4.0.md](docs/releases/3.4.0.md).

Why: an owner on Codex ran `$sql-context-pack doctor` against a hung service and got only an
assessment. Every probe failure became `SERVICE_UNAVAILABLE` with a generic action, so neither
the agent nor the owner could tell a stopped service from a stuck one, or what to do next.

**Added**

- Specific service findings: `SERVICE_DOWN`, `SERVICE_HUNG`, `SERVICE_AUTH_REJECTED`,
  `SERVICE_METADATA_INVALID`, `SERVICE_UNHEALTHY`, `PORT_OWNER_MISMATCH`.
- `diagnosis`, `evidence` and `proposed_fix` on every service finding; evidence is read-only,
  bounded and free of URLs, tokens, metadata and SQL.
- `sqlctx doctor fix --host <host> [--finding <CODE>]`: applies the proposed service-layer fix,
  re-inspects, and reports `fixed` only when no service finding remains and the service reports
  a version. Prints the exact Administrator command when elevation is needed; never stops a
  process the service does not own.
- Skill doctor reference: report the diagnosis and evidence, propose `doctor fix` once, verify.

**Security**

Dependencies were checked against OSV (api.osv.dev) on 2026-10-29. Pins with advisories are
raised to versions that fix them:

- `certifi` floor `>=2026.10.23` (transitive via httpx) — GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320, critical:
  bundled emSign roots with compromised intermediate keys.
- `cryptography` 49.0.0 → 50.0.2 — GHSA-g6cj-pr64-35w5, high: PKCS#7 Bleichenbacher oracle.
- `dnspython` floor `>=2.9.0` (transitive via email-validator) — GHSA-45c3-73f7-pv4m, high.
- `mcp` 1.28.1 → 1.30.0 — four moderate advisories (cross-origin redirect header leak,
  unbounded StreamableHTTP sessions, server-chosen `$ref` fetch, bearer audience check).
- Build: `setuptools` 83.0.0 → 84.2.1 (GHSA-xmj3-9gf7-h52w, low); `packaging` 25.0 → 26.3
  (no advisory; aligns with current tooling so `dev-check` builds).
- Known, unfixed: optional `mariadb` 1.1.14 extra has PYSEC-2026-217 and no fixed release. It is
  not installed by default; prefer the `mysql` extra (PyMySQL) for MariaDB servers until a fix ships.

**Changed**

- Health probe timeout 3 s → 5 s. A redirect from the service port is reported as
  `SERVICE_UNAVAILABLE` and never followed.

## 3.3.1 — 2026-10-16

Patch: SQL Server table catalog fix. Notes: [docs/releases/3.3.1.md](docs/releases/3.3.1.md).

**Repository migration — 2026-10-20**

- Republish 3.3.1 from `cognivex006/sql-context-pack` at the owner's request; update
  canonical install/update URLs, plugin manifests and current guidance so upgrades use the new repository.
- Remove GitHub Actions workflows and disable Actions on the new repository. Run the existing
  local development checks before release instead of hosted CI.
- Record requirement v1.5, retaining v1.4 in full and explicitly superseding its repository/CI rules.
- Keep product, package and Skill version 3.3.1 and output format 2 for this same-version migration.

**Release identity correction — 2026-10-20**

- Use `cognivex006 <cognivex006@users.noreply.github.com>` for new commits and release tags.
- Republish the existing 3.3.1 release from current main so its source includes the corrected
  contributor instructions and README cleanup. Product and Skill versions remain 3.3.1.

**Fixed**

- SQL Server table catalogs failed analysis for every table with `DATABASE_OPERATION_FAILED` on
  databases whose collation is not Latin1 (for example `Thai_CI_AS`). The 3.1.0 table-features
  query concatenated catalog names (database collation) with `*_desc` columns
  (`Latin1_General_CI_AS_KS_WS`) inside a `UNION ALL`, so SQL Server raised error 451 (collation
  conflict) and 8180. Every feature detail is now collated to `DATABASE_DEFAULT`.
- The temporal (2016+) and memory-optimized (2014+) probes now run only on servers that support
  them; a probe that still cannot run is reported as `probe_unavailable` in
  `unsupported_features` instead of failing the table.
- A failed metadata statement now names the statement (`table_features`, `table_columns`, ...)
  and the SQL Server error numbers in the public error, and writes a protected diagnostic under
  `runtime_root/errors` with SQLSTATE and driver message only (no credentials, SQL or data).

## 3.3.0 — 2026-10-15

Requirement [v1.4](docs/spec/design-spec-v1.4.md). Formatting fitted to each of the five
dialects. Migration notes: [docs/releases/3.3.0.md](docs/releases/3.3.0.md).

**Why.** `sqlctx format` defaulted to `ansi`, and one rule set tuned for T-SQL was shared by
every dialect. A per-dialect check against SQLFluff 4.2.2 showed that set still let `CV02`
rewrite Oracle `NVL` and MySQL `IFNULL` (and T-SQL `ISNULL`) to `COALESCE`, which changes
result type and evaluation, while PostgreSQL, MySQL and MariaDB missed safe spacing fixes.

**Added**

- `sqlctx format --profile <name>`: the dialect follows that profile's engine; without
  `--dialect` or `--profile` the dialect is `tsql` (was `ansi`).
- Parse → format → re-parse tests on a realistic sample per dialect against SQLFluff 4.2.2.

**Changed**

- Per-dialect excluded rules: `CP02`, `CV02`, `RF06` everywhere; `LT01` also for `tsql`,
  `oracle` and `ansi`. Exports report the dialect's set in `sqlfluff.exclude_rules`.
- The rule policy is part of the format cache key and the tooling fingerprint.

**Bound.** SQLFluff 4.2.2 cannot lex Oracle `q'[...]'` quoting; such SQL is preserved as
`parse_failed`.

## 3.2.1 — 2026-10-15

Patch. Migration notes: [docs/releases/3.2.1.md](docs/releases/3.2.1.md).

**Why.** The Oracle definition queries used `DBMS_METADATA.GET_DDL('<TYPE>', :2, :1)` and
passed `(schema, name)`. python-oracledb binds SQL placeholders left to right and ignores
their numbers, so GET_DDL received the schema as the object name and the name as the schema.

**Fixed**

- Oracle `table_definition`, `procedure_definition` and `function_definition` bind the schema
  and name in order of appearance and pass them to GET_DDL as `(name, schema)`.
- Requirement integrity test accepts a patch release of the requirement's MAJOR.MINOR,
  so a bug fix no longer needs a new requirement version.
- Contract test simulating python-oracledb's left-to-right binding for all three queries.

## 3.2.0 — 2026-10-15

Requirement [v1.3](docs/spec/design-spec-v1.3.md). Security hardening across all five engines.
Migration notes: [docs/releases/3.2.0.md](docs/releases/3.2.0.md).

**Why.** An owner security review on 2026-10-15 found that SQL Server connection values were
concatenated unescaped (a `;` in a password could switch off TLS verification), that Query Data
on the four non-SQL-Server engines never checked whether the login could write, that some
locking reads passed the validator, that format diagnostics redacted only `password=` forms,
and that Oracle DSNs were built by string concatenation with no way to choose SID or TNS alias.

**Security**

- Brace and escape every ODBC connection value; refuse NUL.
- PostgreSQL, MySQL, MariaDB and Oracle prove a read-only login before Query Data, failing
  closed on any write/admin capability, unknown answer or (MySQL/MariaDB) granted role.
- Refuse `FOR NO KEY UPDATE`, `FOR SHARE`, `FOR KEY SHARE` in addition to `FOR UPDATE` and
  `LOCK IN SHARE MODE`.
- Redact quoted literals and more secret names in format diagnostics; keep rule codes and
  positions, report truncation, add a fingerprint of the unredacted output for tracing.

**Added**

- Optional Oracle profile field `oracle_connect_mode` (`service_name` default, `sid`,
  `tns_alias`), asked by `sqlctx configure`; DSNs built with `oracledb.makedsn`.

**Unchanged**

- Format cache, on the owner's decision. Output format stays `2`.

## 3.1.0 — 2026-10-15

Requirement [v1.2](docs/spec/design-spec-v1.2.md). SQL Server table exports are lossless and
product-neutral. Migration notes: [docs/releases/3.1.0.md](docs/releases/3.1.0.md).

**Why.** A consumer that re-formats tables (for example a product-specific SQL style) needs
every property of the original table. The 3.0.0 table DDL came from a generic renderer that
dropped type lengths (`nvarchar(50)` became `nvarchar`), identity, computed columns, defaults,
collation, FK actions, index order/INCLUDE/filter and column descriptions. The owner decided
that product-specific layout stays out of this package; it only has to provide complete,
neutral metadata.

**Added**

- SQL Server tables capture full types, identity seed/increment, computed definitions,
  non-default collation, sparse, default constraints (real names), key clustered/ASC/DESC,
  check and FK enabled/trusted, FK ON DELETE/UPDATE, index clustered/DESC/INCLUDE/filter, and
  column `MS_Description`.
- Optional `table_metadata` YAML fields for all of the above and `unsupported_features[]`
  (partitioning, filegroup, columnstore, XML/spatial/hash indexes, compression, temporal,
  memory-optimized, filestream, sparse, column sets). Schema in
  [docs/output-format.md](docs/output-format.md#table-metadata). New optional fields make this
  a minor version.
- Table DDL emits `sys.sp_addextendedproperty` for table and column descriptions, and lists
  unrendered features as `-- not rendered:` lines.

**Fixed**

- A composite foreign key was emitted as several constraints with the same name.
- A unique constraint was emitted twice: inline and as `CREATE UNIQUE INDEX`.
- Primary key and unique columns were ordered alphabetically instead of by key ordinal.

**Unchanged**

- Sensitive-data protection from 3.0.0; column descriptions use the same redaction as table
  descriptions. Output format stays `2`.

## 3.0.0 — 2026-10-09

Requirement [v1.1](docs/spec/design-spec-v1.1.md). Sensitive data must never reach an AI
model, and so can never be retained or used to train one. Migration notes:
[docs/releases/3.0.0.md](docs/releases/3.0.0.md).

**Why.** An owner review of 2.1.0 found that classification failed open: an unrecognised
column was public. Measured on 2.1.0, `FIRST_NAME`, `LNAME`, `FARMER_NAME`, `PWD`, `TOKEN`,
`ADDR`, `LATITUDE` and free text in `REMARK` were returned raw, and `ID_CARD`, `DOB` and
`BANK_ACC_NO` were masked only because their values happened to look like phone numbers.
Masked values could also be recovered through `WHERE`/`LIKE`/`LEN`.

**Security — classification and treatment**

- Add Thai/AgriMap and abbreviated column-name rules, camelCase splitting, a `free_text`
  class, and value rules for Thai IDs, Thai and international phones, cards and titled names.
- Read SQL Server `sys.sensitivity_classifications`; any classified column is sensitive.
- Replace names, Thai IDs, phone, card and account numbers with tool-generated fakes that keep
  the original layout. Fake identifiers start with `0` so they can never be a real issued
  number; fake Thai IDs carry a valid check digit. Fakes are HMAC-chosen and deterministic per
  key: random per query for Query Data, the protected snapshot key for exports.
- Generalize address, date of birth (year) and precise location (one decimal place).
- Scan every string and JSON value for embedded personal data, not only whole-value matches.
- Redact personal data in routine string literals and table descriptions as visible
  `[REDACTED:<CLASS>]` marks rather than fakes, so a routine is never redeployed with a fake
  value that looks real.

**Security — inference guard and reveal handoff**

- Reject protected columns outside a plain projection — `WHERE`, `JOIN … ON`, `GROUP BY`,
  `ORDER BY`, `HAVING`, windows, `CASE`, functions, including through aliases — with
  `QUERY_SENSITIVE_USAGE_RESTRICTED` (403).
- Results with protected columns and every restriction error carry `reveal_handoff`
  (`model_may_view: false`) so the user runs the query themselves.
- Add owner CLI `sqlctx query --reveal` (interactive terminal plus typed confirmation; refuses
  non-interactive execution; never wired to MCP or HTTP) and `--sql-file`.

**Contracts**

- `QueryResultColumn` adds `sensitivity`, `treatment`, `marker`; `QueryDataResult` adds
  `protected_value_counts`, `reveal_handoff`. Markdown headers carry the marker.
- `SamplePage` adds `column_markers`; sample headers are marked; export reports add
  `pii_literals_redacted`. `SensitivityClass` adds `free_text`.
- Skill: new "Sensitive data" section — say when a quoted value is fake, never work around the
  guard, relay `reveal_handoff` verbatim, never run `--reveal`.

**Owner decisions recorded in v1.1:** no schema-only mode, no owner override file, no `doctor`
scan of 2.x exports.

## 2.1.0 — 2026-09-17

First released version. The repository history begins here, and every earlier internal
iteration (1.0.3 through 1.6.0) is folded into this entry as the dated development record
below. Those versions were never published as releases.

Dates for the 1.1.0–1.3.0 entries, which were recorded as "Unreleased", are taken from the
commit dates of the work they describe rather than invented.

---

### 2026-09-17 — Release 2.1.0

**Security — Query Data read path**

- Reject `FOR XML` and `FOR JSON`. They collapse an unbounded number of rows into a single
  cell, which escapes the `max_rows` bound entirely, and their masking only held because the
  collapsed column count happened to match the projection count. A runtime test showed a
  single benign-named projection emitting the blob unmasked.
- Reject table and query hints: `TABLOCKX`, `UPDLOCK`, `HOLDLOCK`, `NOLOCK`, `INDEX(...)`,
  and `OPTION (...)`. Each needs only SELECT permission, so `assert_query_read_only` cannot
  prove them safe, and they allow exclusive locks on live tables, uncommitted reads, and
  unbounded recursion.
- Reject non-allowlisted bare functions. Parenthesis-free calls parse as `bare_function`
  rather than `function` and previously skipped the allowlist entirely, exposing
  `CURRENT_USER`, `SYSTEM_USER`, `SESSION_USER` and `USER`.
- Confirmed already-closed before this release: INSERT, UPDATE, DELETE, TRUNCATE, DROP,
  ALTER, MERGE, CREATE, EXEC, `sp_executesql`, OPENROWSET, OPENQUERY, OPENDATASOURCE,
  stacked statements, comments, and cross-database references.

**Context index**

- Synchronize the full discovered inventory on every export instead of only the objects that
  reached a confirmed context. `ContextIndexSyncRequest.mode` separates machine-derived
  `inventory` writes, which need no approval and can neither deactivate a row nor overwrite
  an `OWNER` classification, from supervised `owner` writes, which stay approval-gated.
- Record objects that cannot be classified as `unresolved` rather than omitting them, and
  hand the owner one consolidated list of every unresolved object with a suggested context
  and its evidence, explicitly marked as a suggestion.
- Return `METADATA_CONTEXT_WRITE_SCOPE_REQUIRED` with the exact owner command in
  `details.owner_command` instead of a bare 403.
- Fail with `METADATA_CONTEXT_ENGINE_UNSUPPORTED` on PostgreSQL, MySQL, MariaDB and Oracle
  instead of raising `AttributeError`. Only the SQL Server adapter implements the
  metadata-context protocol, and inventory sync now runs on every export, so the unguarded
  path would have surfaced an internal error on every export against those engines.

**Hosts**

- Add `agy` (Antigravity) as a first-class host alongside `gemini`. `--host` accepts
  `codex|claude|gemini|agy`.
- Install Antigravity by file placement under `~/.gemini/config/skills/` plus a merge into
  `~/.gemini/config/mcp_config.json`, because it has no plugin or extension CLI. The merge is
  additive in both directions: other MCP servers survive install, update and removal.
- Accept only the skill layout for Antigravity; the plugin layout would bury `SKILL.md` one
  directory below where it looks.

**Service and updates**

- Move service supervision into `sqlctx.service.manager` so it is reachable from an installed
  package, and expose `sqlctx service status|start|stop|install|remove`.
- Support Windows as a real platform rather than a delegation stub: the SCM service when one
  is registered, and the portable pidfile supervisor otherwise, so an owner without
  Administrator rights still gets one shared service.
- Add `install.sh` for POSIX hosts so `doctor update` works off Windows.
- Add `--version <x.y.z>` to pin an update to an exact released tag. An unknown version stops
  and reports the available tags rather than falling back to the latest release.

**Repository**

- Commit source the repository had never received. `query_data/lineage.py` and
  `security/transport.py` are imported at runtime but existed only in the working tree, so a
  clean clone failed on import and `pip install` from the repository could not produce a
  working package. The 1.5.0 lineage masking and the 1.6.0 declaration-boundary fix were
  likewise uncommitted, meaning the published code still contained the masking bypass the
  1.5.0 notes described as closed.
- Rewrite `scripts/validate_manifests.py`, which had drifted to a hardcoded `VERSION =
  "1.3.0"` while every shipped surface was at 1.6.0. It and the version contract test now
  derive the expected version from `_version.py` and cover the Claude and Antigravity
  marketplaces.
- Recompute the `design-spec-v1.26` SHA-256 sidecar from canonical LF bytes; it alone had
  been generated from a CRLF working copy, against the repository's own `eol=lf` policy.
- Repoint the Antigravity marketplace at `orchex006/sql-context-pack`, which still referenced
  the pre-transfer repository.
- Remove `.agrimap-agent/` from published history and ignore it.

**Workflow and documentation**

- Add `AGENTS.md` covering `feature/*` and `hotfix/*` branches, annotated `v<x.y.z>` tags, the
  version surfaces that must agree before tagging, and the steps required whenever `skills/`
  changes.
- Add `docs/knowledge/` with repair and installation procedures for the MCP service, SQLFluff
  and the four hosts.
- Rewrite the documentation set in English.

---

### 2026-09-16 — SQL Server routine analysis fix (1.6.0)

- Fix a regression introduced in 1.3.0: declaration canonicalization rejected any routine
  whose definition began with a comment, so every stored procedure or function carrying the
  ordinary SSMS banner failed extraction and vanished from sitemap views and materialization
  plans. On one 590-routine schema, 530 routines failed before the fix and 0 after.
- Locate the declaration boundary past the byte-order mark, whitespace, `--` line comments and
  nested `/* */` block comments, preserving the comment prefix verbatim. Declaration keywords
  found only inside a comment or string are still rejected, and an unterminated comment fails
  closed.
- Rewrite only the declaration keywords. Across all 590 live routines the canonicalized text
  is byte-identical to the source apart from those keywords, so `OUTPUT` parameter direction,
  `@@PROCID` and the routine body are preserved. `OUTPUT`, `@@PROCID` and scalar functions
  were never the cause; 36 passing procedures already used `OUTPUT` and 40 already used
  `@@PROCID`.
- Report analysis failures per object. `CatalogStatus` gains `failures[]` with object id,
  object type, stage (`analysis`, `sample`, `dependencies`), a stable error code and a
  sanitized, length-bounded message that excludes credentials, raw SQL bodies and owner
  absolute paths. It is projected through MCP, HTTP and the export manifest as
  `export.analysis_failures[]`. Failed objects are never auto-excluded.

---

### 2026-09-14 — Query Data lineage, transport and service account (1.5.0)

- Preserve source sensitivity through Query Data aliases and expressions, and conservatively
  redact unresolved lineage, complex wildcard renaming and JSON extraction in both bounded and
  streamed output.
- Restrict bearer transport to validated IPv4 loopback endpoints, and disable proxy
  environment variables and redirects while preserving custom local ports and existing MCP
  commands.
- Replace LocalSystem with a dedicated virtual Windows Service account, a protected CPython
  copy, exact application and configuration ACLs, and scoped registered-folder access, with
  migration rollback retained.
- Add `doctor status/version/check/update`, optional online release checks, host selection and
  verified package/plugin/service upgrade results, preserving the existing `doctor --mcp`
  readiness probe.
- Validate the update remote, branch and worktree; pin CI actions; and provide a solo-owner
  main ruleset for explicit GitHub activation. A checked-in ruleset is not proof that remote
  protection is enabled.

---

### 2026-07-29 — Context index, folder classification and routine deployment (1.3.0)

- Add the one-table SQL Server context index `[agrimap_app].[DB_METADATA_CONTEXT]` with typed
  sync/list/resolve contracts, owner-value precedence, explicit numeric actor IDs, paginated
  filtering, profile write scope, approval gates, and hash-checked generation plans.
- Add registered-folder scan, dry-run classification plans, separate-output atomic apply, and
  approval-gated in-place apply with identity, path, link, collision and content-hash checks.
- Add immutable single-file and recursive-folder routine deployment plans. SQL Server
  procedures and functions use validated `CREATE OR ALTER` declarations; apply rechecks file
  identity, content and current database fingerprints, requires explicit routine write scope
  and owner approval, and fails closed on unsupported engines without DROP/recreate.
- Add versioned managed SQL headers and complete all-mode materialization. Profile-allowed
  TABLE, PROCEDURE and FUNCTION definitions that cannot be classified are retained under
  `unknowns/` without guessed context, description or tags.
- Rework owner context resolution into a hash-bound managed-file plan that atomically
  reconciles the confirmed header and context folder before the same plan is synchronized to
  the database, eliminating DB-only metadata drift.
- Add proven complete-catalog reconciliation with exact inventory matching, soft deactivation
  of missing active rows, empty-scope handling, and truthful
  inserted/updated/unchanged/deactivated accounting. Partial sync never deactivates unrelated
  records.
- Strengthen the `DB_METADATA_CONTEXT` preflight from column-name checks to its exact column
  type, size, precision, scale, nullability, identity, default, check/version and index
  signature, including unexpected objects, disabled or untrusted constraints, index direction,
  triggers and foreign keys.
- Canonicalize SQL Server stored-procedure and function definitions at the declaration
  boundary to `CREATE OR ALTER` without global body replacement.
- Add the corresponding owner CLI, HTTP and MCP surfaces: 34 core MCP tools, 34 HTTP paths and
  4 bridge tools.
- Move new managed output to format version `2`; compatible older validation behavior remains
  explicit.
- Restore exact provider-specific Skill onboarding. Codex uses `$sql-context-pack`, Claude
  Code uses `/sql-context-pack:sql-context-pack`, and Gemini CLI uses `/skills list` plus
  explicit natural-language activation; documentation no longer presents Codex syntax as
  universal.
- Document the 2026-09-14 source and installation security review, including owner-writable
  LocalSystem execution configuration, a reproduced Query Data alias masking bypass, and
  conditional bearer-token transport risks. Evidence and scope are in
  `docs/security-review-2026-09-14.md`.

---

### 2026-07-23 — Query Data (1.2.0)

- Add read-only relational Query Data with dialect-aware parse validation, a function
  allowlist, table resolution against the discovered catalog, literal parameterization,
  deterministic masking, bounded Markdown output and streamed owner CLI output.

---

### 2026-07-20 to 2026-07-21 — Multi-harness install, cross-platform and performance (1.2.0)

- Add `-Harness codex|claude|gemini` to global installation, which previously worked only for
  Codex. Each provider stages its own manifest with the same canonical Skill, installs to that
  provider's own home, and registers through its own CLI verbs. Gemini has no marketplace, so
  its extension directory is the install unit and `--mode skill` is refused with
  `SKILL_MODE_UNSUPPORTED`.
- Improve export performance and add incremental caching with per-object checkpoints, so an
  interrupted export resumes instead of restarting.
- Fix cross-platform issues in the installer and CI, and add uninstall coverage.

---

### 2026-07-19 — Owner installation and profile lifecycle (1.1.0)

- Add a Codex personal-marketplace plugin contract with a mutually exclusive direct global
  Skill fallback, and protected ephemeral MCP configuration for owner-started harness
  sessions.
- Add an interactive host-Python profile wizard that installs safe config files and encrypts
  database host, name, username and password in owner-only runtime storage.
- Add a PATH-independent PowerShell server launcher and a Python module server entry point.
- Add safe `profile list` and protected `harness mcp-list` commands so owners can copy exact
  profile names and verify ephemeral MCP registration.
- Add root `install.ps1` and `sqlctx launch` composition so one explicit owner command can
  configure the first profile, start its loopback child service and open the protected harness
  workflow.
- Add SQL Server DSN-less installed-driver selection (Driver 18 preferred, Driver 17
  fallback), immediate wizard connectivity validation, and safe `profile test` diagnostics.
- Add sanitized per-tool MCP operation events, protected audit storage, server INFO events and
  owner-facing `audit tail` inspection.
- Clarify that SQLFluff is a required runtime dependency while Ruff is optional
  developer/CI-only tooling.
- Remove an unrelated untracked `.agrimap-agent` workflow artifact from the product workspace;
  it was never a SQL Context Pack dependency.

---

### 2026-07-18 — Initial implementation (1.0.3)

- Preserve the authoritative specification and its SHA-256 sidecar, and add
  implementation-routing documents for requirements, architecture, security, output format,
  acceptance, versioning and harness compatibility.
- Add the typed Python package skeleton, public domain models and ports, a central version
  source, a pinned dependency strategy, preflight helpers, CI and three harness manifests.
- Add contract tests for spec integrity, version consistency, public-model safety and path
  validation.
- Add environment-referenced profile loading, protected runtime and credential state,
  encrypted resumable masking keys, deterministic aliases, request-bound approvals, secret
  scanning, host-Python SQLFluff lifecycle and fail-isolated per-file formatting.
- Add reviewed-query adapters for PostgreSQL, MySQL, MariaDB, SQL Server and Oracle, including
  identifier and schema guards, deterministic sampling, capability mapping, cancellation and
  optional lazy drivers.
- Add two-phase full-analysis catalog orchestration with cursor pagination, exact request
  fingerprints, cancellation, retention and quota handling, and dependent-export pinning.
- Add configurable two-pass classification, request-bound persistent owner overrides,
  validated non-authoritative model proposals, classification-change tracking and final
  materialization planning.
- Add relationship and cardinality graph-ready indexes, deterministic SQLFluff-scoped bundles,
  managed-file manifests, assembled-output re-read validation, and a hash-valid realistic
  output fixture.
- Add the complete loopback HTTP surface, strict structured MCP tools and resources, a shared
  typed facade, caller-scoped idempotency, persistent request-bound owner approvals, a
  protected binary fetch CLI, and HTTP/MCP contract examples and parity tests.
- Add the canonical Agent Skill with exact contract and safety references, deterministic
  multi-batch assembly, managed-only repeated updates, nested-output end-to-end coverage,
  bundle traversal rejection and destination-corruption checks.
- Add Codex, Claude Code and Gemini CLI packaging around one canonical Skill, generated
  OpenAPI and MCP schemas, deterministic cross-harness conformance simulation,
  installed-harness smoke validation, and operator and developer documentation.
- Add release wheel and source distribution verification, package-install smoke coverage,
  artifact hashing and a checked-in release report.
- Bind final catalog accounting and sitemap categories to Pass 2 classifications rather than
  preliminary name-based categories.
- Make server validation fail closed on count equations, submitted-inventory hashes, bundle
  hashes and embedded manifest hashes.
- Publish exact typed response schemas for all structured HTTP and MCP operations, and retain
  binary export transfer exclusively on authenticated HTTP and CLI.
