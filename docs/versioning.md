# Versioning

| Surface | Current |
|---|---|
| Product, package, Skill, and all four host manifests | `3.5.4` |
| Output format | `2` |
| Requirement | `1.8` |
| SQLFluff | `4.2.2` |
| MCP SDK | `1.30.0` |

## Product version

The product follows SemVer.

- **Major** — an existing contract breaks: an MCP tool is removed or changes meaning, the
  output format version changes, the managed header shape changes, or a CLI command or option
  is removed.
- **Minor** — a backward-compatible surface is added: a new MCP tool, a new CLI option, a new
  host, or a new optional field.
- **Patch** — a bug, security or documentation fix that adds no new surface.

2.1.0 is the first released version. Versions 1.0.3 through 1.6.0 were internal development
iterations and were never published; their history is summarised by date in
[CHANGELOG.md](../CHANGELOG.md).

## Output format version

The managed file contract carries its own version, separate from the product version, because a
change to the header or layout affects files already on disk. It changes only when a managed
file's bytes would no longer validate under the previous rules.

## Requirement version

Requirement versions are additive. A new version retains the previous version's content in full
unless the owner explicitly changes or removes a rule. Every version ships a SHA-256 sidecar and
is stored with canonical LF bytes. See [Requirements](requirements.md).

## Keeping surfaces in agreement

These must all carry the same product version, and `scripts/validate_manifests.py` fails the
build if any of them drifts:

| Surface | Path |
|---|---|
| Package | `src/sqlctx/_version.py` |
| Build metadata | `pyproject.toml` |
| Skill | `skills/sql-context-pack/SKILL.md` |
| Codex | `.codex-plugin/plugin.json` |
| Claude Code | `.claude-plugin/plugin.json` and `marketplace.json` |
| Gemini CLI | `gemini-extension.json` |
| Antigravity | `.agents/plugin.json` and `plugins/marketplace.json` |
| This page | `docs/versioning.md` |

`docs/generated/*.json` must be regenerated from the code, not edited, whenever a contract
changes.

`CHANGELOG.md` records what shipped. It is not a substitute for the requirement history, and the
requirement history is not a substitute for it.

## Same-version repository migration

On 2026-10-20 the owner approved republishing 3.3.1 from
`cognivex006/sql-context-pack`, with a new annotated `v3.3.1` tag on the migrated main commit.
GitHub Actions workflows are removed and Actions disabled in the new repository.
Release checks run locally, including a clean-clone verification before committing.
The migration changes repository locations, not the MCP/CLI/output contracts; every product
version surface remains 3.3.1 by explicit owner instruction.

The owner also approved recreating the existing `v3.3.1` tag and release from the updated
main after the README cleanup and commit-identity correction. New commits and the annotated
tag use `cognivex006 <cognivex006@users.noreply.github.com>`. Historical commit authors and
archived requirement/prompt text retain their original attribution.

## 3.5.4 — metadata read timeout

A patch version: catalog, sample and dependency reads carry a 120-second statement timeout, so a
read blocked by a lock fails that one object instead of stalling the whole catalog. No
requirement version: it fixes 3.5.3 against requirement [v1.8](spec/design-spec-v1.8.md) and adds
no surface. Output format stays 2.

## 3.5.3 — extraction and formatting performance

Requirement [v1.8](spec/design-spec-v1.8.md), phase 1 of the 4.0.0 track. A patch version: metadata
reads reuse one connection per worker, analysis reads run on at most four sessions, and SQLFluff
runs in long-lived workers instead of three interpreter launches per object. No tool, option,
field or output byte changes, so nothing here qualifies as a minor version. Output format stays 2.

## 3.5.2 — Antigravity install on Windows

A patch version: `install.ps1 -Harness agy` installs the Skill layout instead of failing parameter
validation in `scripts/install-global.ps1`. No requirement version: it fixes the installer against
requirement [v1.7](spec/design-spec-v1.7.md) and adds no surface. Output format stays 2.

## 3.5.1 — metadata check comparison fix

A patch version: `DB_METADATA_CONTEXT` check constraints are compared by meaning instead of
text, so the forms SQL Server stores no longer read as schema drift. No requirement version: it
fixes 3.5.0 against requirement [v1.7](spec/design-spec-v1.7.md) and adds no surface. Output
format stays 2.

## 3.5.0 — filtered scope and local hardening

Requirement [v1.7](spec/design-spec-v1.7.md). A minor version: it adds the `filtered` selection
mode, the `filtered_scope` inclusion reason, the optional `unmatched_wildcard_patterns` catalog
status field, and the `CREDENTIAL_KEY_UNWRAPPED` and `UPDATE_SIGNATURE_UNVERIFIED` findings. The
unauthenticated `/api/docs` and `/api/openapi.json` routes are removed; they were never part of
the documented contract. Output format stays 2.

## 3.4.0 — doctor diagnose and fix

Requirement [v1.6](spec/design-spec-v1.6.md). A minor version: it adds the `doctor fix` action,
the `--finding` option, new service finding codes, and the optional `diagnosis`, `evidence` and
`proposed_fix` fields on service findings. No MCP tool, output format or existing CLI option
changes. `SERVICE_UNAVAILABLE` is kept for faults doctor cannot classify.
