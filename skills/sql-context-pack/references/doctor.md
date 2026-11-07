# Doctor and upgrades

Resolve the selected host from the current environment/user request: codex, claude, gemini or agy.
Never infer host from the model name. Claude Code is `claude`, Codex is `codex`, Antigravity is
`agy`, and legacy Gemini CLI is `gemini`. The CLI defaults to codex; pass --host for another host.

`agy` (Antigravity) is Google's successor to Gemini CLI. It has no plugin or extension CLI, so it
installs by file placement under `~/.gemini/config/skills/` plus a merge into
`~/.gemini/config/mcp_config.json`, and its reported version comes from the installed SKILL.md.
See [docs/knowledge/hosts.md](../../../docs/knowledge/hosts.md).

| Request | Command | Effect |
| --- | --- | --- |
| doctor / status | `sqlctx doctor --host codex` | Read installed/source/plugin/service versions, Python/SQLFluff and safe profile readiness |
| version | `sqlctx doctor version --host codex` | Print package version only; no network probe |
| check | `sqlctx doctor check --host codex` | Read local readiness and report drift |
| check online | `sqlctx doctor check --online --host codex` | Also read latest GitHub release metadata; no Git/cache refresh |
| MCP check | `sqlctx doctor --mcp --host codex` | Preserve existing authenticated tool-list readiness probe |
| fix | `sqlctx doctor fix --host codex [--finding SERVICE_HUNG]` | Apply the proposed service fix, then re-inspect and verify |
| update | `sqlctx doctor update --host codex` | Explicitly update selected host package and shared local runtime |
| update to a version | `sqlctx doctor update --host codex --version 2.1.0` | Pin the update to one released tag |
| service state | `sqlctx service status` | Report the one shared service and which supervisor owns it |

Default/status/version/check are read-only: do not install, refresh caches, change profiles, open
database connections or start a lifecycle just to answer them. Report errors and version drift;
never include connection metadata contents, tokens or credentials in the response.

## Diagnose, propose, fix

Doctor names the service fault; do not stop at an assessment. For every finding whose
`proposed_fix` is present:

1. Tell the owner the `code`, the `diagnosis` and the relevant `evidence` (port, listener PID
   and start time, SCM PID/state, `CLOSE_WAIT` count). Never add URLs, tokens or metadata.
2. Propose `proposed_fix.command` once, with its `effect`, and say when `requires_elevation`
   is true that it must run in an Administrator PowerShell. Do not run it unasked.
3. When the owner types `doctor fix` (or approves it), run exactly that command. It is
   authorised; do not ask for a second generic confirmation.
4. `DOCTOR_FIX_NEEDS_ELEVATION` means this session lacks the rights: give the owner the
   printed command and wait; do not try another way around it.
5. `fix_id: none` (`PORT_OWNER_MISMATCH`) means another process holds the port. Name the PID;
   never stop it yourself.
6. Report `fixed` only from `doctor fix` output `status: fixed`, then run
   `sqlctx doctor check --host <host>`. Fix the service layer first; version drift is a separate
   `doctor update` step afterwards.

| Code | Means | Proposed fix |
| --- | --- | --- |
| `SERVICE_DOWN` | nothing listens on the port (or SCM RUNNING with its server gone) | start, or restart when SCM still reports RUNNING |
| `SERVICE_HUNG` | port accepts, health does not answer | restart; force-stop only our own stuck process |
| `SERVICE_AUTH_REJECTED` / `SERVICE_METADATA_INVALID` / `SERVICE_UNHEALTHY` | token or health problem | `sqlctx repair --component service` |
| `PORT_OWNER_MISMATCH` | another process owns the port | none: owner stops that PID |
| `CREDENTIAL_KEY_UNWRAPPED` | a Windows master key is not machine-protected yet | none: connect a profile once; never delete or recreate the key |
| `UPDATE_SIGNATURE_UNVERIFIED` | the pinned release tag has no verifiable signature | report it; the update still used the canonical origin |

Upgrading a machine below 3.5.0: follow
[docs/knowledge/upgrade-to-3.5.0.md](../../../docs/knowledge/upgrade-to-3.5.0.md) layer by layer.

An explicit update authorizes the update action. Run the exact owner CLI command; do not ask for
another generic confirmation. Windows may request elevation for protected installation files and
service registration. Preserve profiles, credentials, registered folders and retained work.

`--version <x.y.z>` pins the update to exactly that released tag. A version that does not exist is
a stop that reports the available tags; never fall back to latest silently, and never round a
request for one version into another. Omitting `--version` installs the latest release.

Update runs on every platform: Windows uses `install.ps1` and the SCM service, and other hosts use
`install.sh` with the systemd/launchd/pidfile supervisor selected by `sqlctx service`.

When install provenance is missing (a plugin from the host's own marketplace), update fetches the
latest release's one-line updater, accepts it only if it matches the release `SHA256SUMS`, and runs
it for the selected host. Without provenance only the latest release can be installed: a
`--version` that is not the latest stops with `UPDATE_SOURCE_REQUIRED` and its `details.latest`;
report that and offer `--source`. Never retry it as an unpinned update yourself.

Update accepts `--source <trusted-checkout>` to pin any release. It requires a clean
main checkout tracking origin/main of cognivex006/sql-context-pack. A dirty/foreign source is a stop;
never delete changes, change remotes or reset branches automatically. `sqlctx repair --source ...`
is for an explicitly reviewed local build and deliberately performs no Git download.

Report success only when installed package, selected plugin and running service versions match.
A successful installer exit alone is insufficient. Reopen the host session to load changed Skill
instructions. Package upgrade does not authorize publishing a GitHub release or changing unrelated
host software. The shared service upgrade also benefits other installed hosts; do not reinstall
their plugins unless selected.
