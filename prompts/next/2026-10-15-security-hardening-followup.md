# Next session — follow-up to 3.2.0 security hardening

Paste everything below the line into a new session in this repository.

---

Continue the SQL Context Pack 3.2.0 work (requirement v1.3, PR
https://github.com/orchex006/sql-context-pack/pull/3, branch `feature/security-hardening-v1.3`).
Follow AGENTS.md throughout: record my prompts, branch rules, commit identity `orchex006`,
`scripts/dev-check.ps1` + `scripts/validate_manifests.py` before every commit, clean-checkout
check, honest reporting.

State at handoff (2026-10-15): 3.2.0 is released. PR #3 is merged (7da5078), tag `v3.2.0` and
the GitHub release are published. Supported engines stay exactly: SQL Server, PostgreSQL, MySQL,
MariaDB, Oracle — do not add Snowflake, BigQuery or any other dialect.

Do these in order, and stop to ask me where marked.

1. **Housekeeping.** Make sure PR `feature/handoff-prompts-3-2-0` (prompt history and this
   file) is merged. The remote branch `feature/security-hardening-v1.3` is merged but not
   deleted; **ask me** before deleting it.

2. **Check the Oracle bind order (possible existing bug).** In
   `src/sqlctx/adapters/oracle/adapter.py`, `table_definition`, `procedure_definition` and
   `function_definition` use `DBMS_METADATA.GET_DDL('<TYPE>', :2, :1)` while parameters are
   passed positionally as (schema, name). python-oracledb binds SQL positionally by order of
   appearance, so `:2` may receive the schema. Prove it with a test against the driver's
   documented behaviour (or a live Oracle if one is available). If it is a bug, fix it on a
   `hotfix/oracle-ddl-bind-order` branch from the latest tag → patch 3.2.1, with release notes.
   If it is not a bug, report the evidence and change nothing.

3. **Formatting for the five dialects** (analysis was presented on 2026-10-15; **ask me to
   approve** before building — building it creates a new requirement version):
   - `sqlctx format` defaults `--dialect` to `ansi`; propose defaulting to the active profile's
     engine dialect, falling back to `tsql`.
   - `--exclude-rules CP02,LT01,RF06` is shared by every dialect but was tuned for T-SQL;
     propose per-dialect rule sets.
   - Add parse → format → re-parse tests with a realistic sample per dialect (`tsql`,
     `postgres`, `mysql`, `mariadb`, `oracle`) against the pinned SQLFluff 4.2.2, so none rolls
     back.

4. **Live verification (report only, no code unless something fails).** If I can provide test
   databases, run `sqlctx_query_data` once per engine with (a) a SELECT-only login → must pass,
   and (b) a login with INSERT → must fail with `QUERY_READ_ONLY_CONTEXT_REQUIRED`. Also connect
   Oracle once per `oracle_connect_mode` (`service_name`, `sid`, `tns_alias`). Never ask me for
   credentials in chat; I configure profiles myself with `sqlctx configure`.

Known bounds to keep stating, not hide: MySQL/MariaDB refuse any login with a granted role;
diagnostics do not redact unquoted literals; the read-only checks were tested only against
recorded catalog answers.
