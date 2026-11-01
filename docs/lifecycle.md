# Install, upgrade, repair and uninstall

Four layers must be at the same revision: the host plugin or extension, the owner Python
package, the shared loopback service, and the MCP bridge. Refreshing only the plugin cache is
not a complete lifecycle operation, and is the most common cause of "the tools are missing".

## Prerequisites

- Windows, Linux, macOS or another Unix host with Python `3.11+` on `PATH`.
- Windows: PowerShell. Administrator is requested only for Windows Service registration and the
  ProgramData ACL — and only if you want the SCM service. Without Administrator the portable
  supervisor is used instead.
- Linux: `systemd --user` when available, otherwise a supervised background process.
- macOS: a user LaunchAgent when available, otherwise a supervised background process.
- Git, only to install from a source checkout. The one-line setup does not need it.
- A driver for your database. SQL Server uses ODBC Driver 18, falling back to 17.

No virtual environment is needed or wanted. The owner package installs with `pip --user`,
Linux/macOS/Unix never use root or `sudo`, and every platform stages under the OS temporary
directory.

## One-line setup

The shortest path on a new machine, and the simplest way to update one:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/cognivex006/sql-context-pack/releases/latest/download/install.ps1 | iex"
powershell -ExecutionPolicy Bypass -c "irm https://github.com/cognivex006/sql-context-pack/releases/latest/download/update.ps1 | iex"
```

```bash
curl --progress-bar -fL https://github.com/cognivex006/sql-context-pack/releases/latest/download/install.sh | sh
curl --progress-bar -fL https://github.com/cognivex006/sql-context-pack/releases/latest/download/update.sh | sh
```

There is no version option: these always take the latest published release. To pin an exact
version, use `sqlctx doctor update --version` from a checkout instead.

What they do, in order:

1. Find Python `3.11+` with pip, in the same order the release installer uses.
2. Pick the hosts. `install` takes every host CLI on `PATH` (`codex`, `claude`, `agy`,
   `gemini`) plus any host that already has the plugin. `update` takes only hosts that already
   have it, and never adds one.
3. Resolve the latest release tag once, download `sql-context-pack-install.zip` and its
   `.sha256` from that tag, and stop on a mismatch before anything is unpacked.
4. Unpack it to `%LOCALAPPDATA%\Programs\sql-context-pack\releases\<version>` on Windows, or
   `${XDG_DATA_HOME:-~/.local/share}/sql-context-pack/releases/<version>` elsewhere. That folder
   is the install source recorded for `sqlctx repair`, so it is kept.
5. Run that release's own `install.ps1 -SkipConfigure` or `install.sh` for each host. Nothing is
   reimplemented, so the setup behaves exactly like the checkout installer: the same wheel and
   fingerprint caches, the same Windows Service, the same UAC prompt.
6. Run `sqlctx doctor check --host <host>` for each host. Success means package and service are
   both at the release version, and no plugin this setup registered reports another version.
7. Remove older release folders that no host's install provenance still points at.

While it runs, two terminal rows show an overall bar and a spinner with the current stage and
its elapsed time; a download shows bytes received. Without a terminal it prints one line per
stage. The full installer output goes to `logs\install.log` or `logs/update.log` beside the
`releases` folder, and a failure prints its last lines and the log path.

A host that already lists the plugin from another marketplace — `sql-context-pack@sql-context-pack`
from the GitHub marketplace, or a developer's local checkout — keeps that registration. Only the
package and service are installed for it, so the host never shows the Skill twice, and the
summary prints the command that refreshes that marketplace if its plugin is behind.

Environment overrides:

| Variable | Effect |
|---|---|
| `SQLCTX_HOST=codex,claude` | Use exactly these hosts instead of detecting them |
| `SQLCTX_DRY_RUN=1` | Download and verify the release, print the plan, change nothing |

The setup never asks for a credential and never creates a profile. When no profile exists it
ends by suggesting `sqlctx profile configure`. On Linux it stops early, with guidance, on a
system Python that PEP 668 marks as externally managed, unless `PIP_BREAK_SYSTEM_PACKAGES` is
set; it never creates a virtual environment instead. It does not edit shell profiles: if the
user scripts folder is not on `PATH`, the summary prints the line to add.

`codex plugin list` can take more than a minute on some machines, longer than `doctor` waits.
When it does not answer, the summary says the plugin version was not reported rather than
calling it verified; check it with `sqlctx doctor check --host codex`.

## Install

Pick a host first: [Codex](providers/codex.md), [Claude Code](providers/claude-code.md),
[Antigravity](providers/antigravity.md) or [Gemini CLI](providers/gemini-cli.md). Then run the
Skill setup, which uses the installer from the plugin cache.

| Host | Invocation |
|---|---|
| Codex | `$sql-context-pack setup` |
| Claude Code | `/sql-context-pack:sql-context-pack setup` |
| Antigravity | ask in natural language; confirm the server with `/mcp` |
| Gemini CLI | `/skills list`, then "Use the sql-context-pack skill to run setup." |

Antigravity and Gemini CLI have no repository-specific slash command.

From a source checkout you have reviewed:

```powershell
.\install.ps1
```

```bash
./install.sh
```

The installer runs a Python preflight, builds a wheel when the fingerprint changed, installs the
package and bridge, opens the secure profile wizard if no profile exists, installs the service
bound to `127.0.0.1`, and health-checks before reporting success.

`install.sh` accepts `--harness <codex|claude|gemini|agy>`, `--update`, `--repair`, `--mode`,
`--skip-service` and `--port`.

## Upgrade

A machine set up with the one-line installer updates with the matching
[one-line update](#one-line-setup). From a checkout, the supported path is one command:

```bash
sqlctx doctor update --host <codex|claude|gemini|agy>
sqlctx doctor update --host agy --version 2.1.0   # pin an exact release
```

A version that does not exist as a tag is a stop that lists the available tags. It never falls
back to the latest release.

Update works on every platform: Windows drives `install.ps1` and the SCM service, other hosts
drive `install.sh` and the supervisor that `sqlctx service` selected.

Always open a new session afterwards — Skill instructions load at session start.

If an older profile lacks `FUNCTION`, widen it yourself with `sqlctx profile scope`. Scope is
never widened automatically.

## Repair

Diagnose before changing anything:

```bash
sqlctx doctor --host <host>
sqlctx service status
```

Then repair one layer at a time — service, package, plugin, skill — verifying between each.
Changing several at once makes it impossible to tell what actually fixed it.

```powershell
.\install.ps1 -Repair
.\install.ps1 -Repair -RepairComponent mcp
.\install.ps1 -Repair -RepairComponent package
.\install.ps1 -Repair -RepairComponent service
```

```bash
./install.sh --repair
sqlctx service status
sqlctx service start
```

Full procedures are in [the service knowledge base](knowledge/mcp-service.md). Never delete a
profile, credential, registered folder or retained job to "clean up" a problem.

## Uninstall

Run this from the installed plugin or extension root, before deleting the bundle.

```powershell
.\scripts\lifecycle.ps1 -Operation uninstall -Harness codex
.\scripts\lifecycle.ps1 -Operation uninstall -Harness claude
```

```bash
sqlctx service remove
python3 -m pip uninstall sql-context-pack
```

Then remove the host registration:

```bash
codex plugin remove sql-context-pack@sql-context-pack
claude plugin uninstall sql-context-pack@sql-context-pack
gemini extensions uninstall sql-context-pack
```

For Antigravity, delete `~/.gemini/config/skills/sql-context-pack/` and remove the
`sql-context-pack` entry from `~/.gemini/config/mcp_config.json`. Leave the other entries alone.

Add `-KeepNativePlugin` on Windows to keep the native plugin temporarily.

Encrypted profiles and retained runtime data are deliberately preserved. Deleting them is a
separate owner decision, not part of uninstall.

## Readiness checklist

- `sqlctx doctor` passes, and package, plugin and service versions all match.
- `sqlctx --help` lists this revision's commands: `folder`, `context-index`, `routine` and
  `service`.
- The selected profile reports `ready=true` and its connection test passes.
- The generated contract reports 34 core MCP tools, and the bridge adds 4.
- A new session sees the same Skill and tool set.
- `metadata_context_write` and `routine_write` are still `false` unless you enabled them.

## Version 3.0.0

See the [release and migration notes](releases/3.0.0.md) before upgrading: Query Data now
returns marked fakes and rejects protected columns in filters and functions. Full history is
in [CHANGELOG.md](../CHANGELOG.md).

## Version 2.1.0

See the [release and migration notes](releases/2.1.0.md) before upgrading: Query Data rejects
constructs that 1.6.0 accepted, and `sqlctx_sync_context_index` gained a `mode`. Full history is
in [CHANGELOG.md](../CHANGELOG.md).
