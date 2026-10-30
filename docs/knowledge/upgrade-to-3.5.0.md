# Knowledge — upgrading an existing machine to 3.5.0

For an agent or owner bringing a machine that runs any release below 3.5.0 up to 3.5.0. Read it
whole before running anything. Follow the repair principles in [AGENTS.md §9](../../AGENTS.md):
diagnose first, one layer at a time, verify between layers, never delete a profile, credential,
registered folder or retained job.

## What 3.5.0 changes on an installed machine

| Change | Effect on a machine below 3.5.0 | Owner step |
|---|---|---|
| Machine-bound master keys (Windows) | `master.key` files under the service runtime are plain owner-only files; a copied runtime folder could be decrypted elsewhere | none — the 3.5.0 service protects each key the next time it opens it |
| API schema routes removed | `/api/docs` and `/api/openapi.json` answered without a bearer | none — gone once the **service** runs 3.5.0 |
| Release tag signature reported | updates did not look at tag signatures | none — `doctor update` reports `UPDATE_SIGNATURE_UNVERIFIED` until releases are signed |
| `filtered` selection mode | agents had no valid way to export “all objects named LM_*” | none — load the new Skill in a new host session |

Nothing in 3.5.0 needs a profile, credential or registered folder recreated.

## Step 1 — find where the machine is

```bash
sqlctx doctor check --host <host>
```

Record `versions.package`, `versions.plugin` and `versions.service`. They are three separate
layers; upgrading one does not upgrade the others.

## Step 2 — package

| `versions.package` | Path |
|---|---|
| 3.3.1 or newer | `sqlctx doctor update --host <host> --version 3.5.0` |
| 3.3.0 or older | its update refuses the source with `UPDATE_REMOTE_NOT_TRUSTED`, because it still expects the repository's former name. Do **not** edit the remote or the trust check. Install 3.5.0 once from a checkout of tag `v3.5.0`: `git archive v3.5.0` into a new folder under the OS temp directory, `<host-python> -m pip install --user <that folder>`, then remove the folder. From then on `doctor update` works normally. |

Verify: `sqlctx doctor version --host <host>` reports `3.5.0`.

## Step 3 — service

The Windows service runs from its own runtime under `%ProgramData%\SQLContextPack`, not from the
owner's Python. Upgrading the package does not change it, and `doctor fix SERVICE_DOWN` only
restarts the version already installed. From an Administrator PowerShell:

```bash
sqlctx repair --component service --source <checkout at tag v3.5.0>
```

If it reports `UPDATE_SOURCE_DIRTY`, the checkout has local edits: move them aside (for example
`git stash`), run it, then restore them. Never delete them.

Verify: `sqlctx doctor check --host <host>` reports `versions.service` = `3.5.0` and no
`VERSION_DRIFT` for the service.

## Step 4 — plugin

Upgrade through the host's own plugin CLI, then open a new host session (tools and the Skill are
read at session start):

- Claude Code: `claude plugin marketplace update sql-context-pack`, then
  `claude plugin update sql-context-pack@sql-context-pack`.
- Codex: `codex plugin marketplace upgrade sql-context-pack`, then
  `codex plugin add sql-context-pack@sql-context-pack`. The Codex marketplace entry fetches the
  plugin from the repository's `main`, so the release must be merged and pushed first.
- A marketplace still pointing at the former repository name or pinned to an old tag must be
  removed and added again from `cognivex006/sql-context-pack`. Removing a marketplace drops only
  host plugin settings, never sqlctx profiles or credentials.

Verify: the host's plugin listing shows `3.5.0`.

## Step 5 — master keys (Windows)

```bash
sqlctx doctor check --host <host>
```

`CREDENTIAL_KEY_UNWRAPPED` with `unwrapped_key_count` means the 3.5.0 service has not opened that
key yet. Connect any profile (or run any export); the service converts the key in place, keeping
the same key bytes, so every stored credential stays readable. Run doctor again; the finding
disappears.

Never do any of these to clear the finding:

- delete, rename or regenerate `master.key` — every stored credential becomes unreadable;
- copy a key file from another machine;
- re-create profiles to “reset” encryption.

If the finding stays after a profile connect, report it with the doctor output and stop. A
converted key opens only on this machine: restoring the runtime folder onto a different or
reinstalled Windows installation needs the profiles' credentials entered again.

## Step 6 — report

Report all three versions from `sqlctx doctor check --host <host>`. Installer exit code 0 is not
success; success is package, plugin and service all at 3.5.0 with no `VERSION_DRIFT`.
