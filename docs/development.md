# Development

Read [AGENTS.md](../AGENTS.md) first for the branch, commit and release rules. This page covers
how to run the checks.

## Local verification

Python `>=3.11`. Never create a `.venv` inside the repository.

```powershell
python -m pip install --user -e ".[dev,all-databases]"
.\scripts\dev-check.ps1 -Task all
```

`dev-check.ps1` runs format check, lint, mypy, pytest and build. It keeps every cache and build
directory under the OS temp directory and removes `__pycache__`, `.pytest_cache`, `.mypy_cache`,
`.ruff_cache`, `build`, `dist` and `*.egg-info` in a `finally` block — whether the run passed or
failed.

Run a single stage with `-Task format|lint|typecheck|test|build`.

### Typecheck targets every platform

`-Task typecheck` runs mypy three times: `--platform linux`, `darwin` and `win32`.

This is not belt-and-braces. CI runs on Linux while most development here happens on Windows,
and mypy only analyses the branches that apply to the platform it is targeting. Code guarded for
one platform is therefore invisible on the other — `msvcrt` does not exist in the Linux stubs,
`fcntl` does not exist in the Windows stubs, and `os.stat_result.st_file_attributes` is
Windows-only. Checking just the host lets those errors reach CI.

Two related rules:

- Guard platform-specific code with `sys.platform == "win32"`, **not** `os.name == "nt"`. mypy
  narrows on `sys.platform` and ignores `os.name`.
- Read a platform-only attribute with `getattr(obj, "name", default)`. A `hasattr` check does not
  help, because mypy cannot narrow an attribute its target stubs omit.

On a POSIX host, run the equivalents directly:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m ruff format --check .
PYTHONDONTWRITEBYTECODE=1 python -m ruff check .
PYTHONDONTWRITEBYTECODE=1 MYPYPATH=src python -m mypy --platform linux
PYTHONDONTWRITEBYTECODE=1 MYPYPATH=src python -m mypy --platform darwin
PYTHONDONTWRITEBYTECODE=1 MYPYPATH=src python -m mypy --platform win32
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider --basetemp "$(mktemp -d)" tests
```

Keep the basetemp path short on Windows; a long one can exceed `MAX_PATH` and produce failures
that look like real test errors but are not.

## Generated contracts

```powershell
python scripts/generate_contract_schemas.py
python scripts/validate_manifests.py
```

Never hand-edit `docs/generated/*.json`. Current expected totals: 38 HTTP operations, 34 core MCP
tools, 4 bridge tools and 2 MCP resources.

`validate_manifests.py` derives the expected version from `src/sqlctx/_version.py` and checks
every manifest, marketplace and discovery pointer against it. If you add a new version surface,
extend that script — do not verify by eye.

## One-line installer assets

`installer/` holds the four bootstraps the one-line commands download. `install.*` and
`update.*` differ only in their operation line; `tests/contract/test_one_line_installer.py`
fails if they drift apart, if a bootstrap stops being ASCII, or if a `.sh` gains CR bytes. The
same tests dry-run both bootstraps against a local fake release, including a checksum mismatch
and a release published without assets.

The bootstraps read `releases/latest/download/`, so every release needs its assets uploaded,
after the tag is on `main` and the GitHub release exists:

```powershell
python scripts/build_release_assets.py --tag v<version> --upload
```

That builds `sql-context-pack-install.zip` from `git archive` of the tag with LF bytes, its
`.sha256`, the four bootstraps from `HEAD` and a `SHA256SUMS`, uploads them with
`gh release upload --clobber`, and removes the build folder. Use `--output <empty-folder>`
instead of `--upload` to inspect the files first. A release without these assets makes every
one-line install fail with "The latest release has no sql-context-pack-install.zip asset yet".

To watch a bootstrap without changing anything, set `SQLCTX_DRY_RUN=1` before running it.

## SQL artifact

```powershell
sqlfluff format --exclude-rules "CP02,CV02,LT01,RF06" --dialect tsql sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql
sqlfluff lint   --exclude-rules "CP02,CV02,LT01,RF06" --dialect tsql sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql
```

SQLFluff is pinned. It is not only a formatter here — Query Data uses its parser to prove a
query is read-only, so a different version changes guardrail behaviour. See
[the SQLFluff knowledge base](knowledge/sqlfluff.md).

## Testing rules

Development tests use fakes only. Never connect to or deploy against an owner database. DDL
deployment is a separate DBA action after review.

Verify against a clean clone, not just your working tree. Files that exist locally but were never
committed will pass locally and fail for everyone else — that failure mode has already shipped
broken code from this repository once.

## Test slices

- Managed header round-trip, body preservation, and unknown materialization
- Folder traversal, symlink, collision, drift and approval handling
- Query Data validation: writes, DDL, execution, external access, result-shaping clauses, hints
  and the function allowlist
- Query Data lineage: a sensitive column must not be declassifiable through an alias,
  expression, CTE, union, wildcard rename or JSON extraction
- One-table DDL, context validation, owner precedence and pagination
- Inventory versus owner sync modes, and the per-engine boundary
- Generation index, header and body drift
- Routine single-file and folder plans, approval, `CREATE OR ALTER`, unsupported engines
- Service supervision across host operating systems
- Exact API and MCP counts, previous-contract preservation, provider manifests and documentation
  links
