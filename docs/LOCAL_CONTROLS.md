# Local controls (IL-008)

This is a local control surface, not the integrated courier. No CLI command here
executes a provider, publishes to GitHub, sends a Chat message or reads the
clipboard. Import and trusted transport ingestion share `ControlService.admit`.
The integration owner must use `ControlService.dispatch`, which revalidates local
authority immediately before the broker's execution transition.

## Configuration and commands

Use the version-1 example in `src/interloc/policy/example_config.json` as a local
configuration template. Replace the synthetic identifiers through explicit local
enrollment. Do not use this source repository as an implicit runtime mailbox.
The default configuration is `%LOCALAPPDATA%\Interlocuator\config\enrollment.json`.
`--config` selects another explicitly local JSON file. Exact fields, bounded
input and duplicate-key rejection apply to configuration as well as requests.

Run each PowerShell command **separately**, from the installed environment:

```powershell
interloc --help
interloc doctor --json
interloc status --json
interloc request import .\reviewed-request.json
interloc request show REPLACE-WITH-REQUEST-UUID
interloc approve REPLACE-WITH-REQUEST-UUID
interloc deny REPLACE-WITH-REQUEST-UUID
interloc revoke REPLACE-WITH-REQUEST-UUID
interloc pause
interloc resume
```

The placeholder UUID and file must be replaced before running those commands.
Global options come before the subcommand, for example
`interloc --home "$env:LOCALAPPDATA\InterlocTest" --config .\local-enrollment.json status`.
A custom home must be absolute, outside every project worktree, and must not pass
through a symlink or junction. `doctor` and an uninitialized `status` are read-only.
Other controls may initialize the local state directory; they never initialize
or authenticate a remote mailbox.

`approve` displays the exact request digest, capability, device, enrolled scope,
repository ID, outbox branch, epoch, effect and expiry. Type the complete digest
in an interactive terminal. Enter alone cancels. Redirected stdin is rejected;
there is no `--yes` or automatic approval flag. Approval makes a request eligible
for the broker; it does not execute it. Its lifetime is at most the request's
remaining lifetime (normally 120 seconds, with an internal 300-second ceiling).
A changed request, target, policy or destination invalidates the preview.

Only ping has a default local target identity at this stage. Terminal and visual
capabilities require a locally enrolled provider supplied by the integration
layer. An unavailable provider is reported as `TARGET_UNAVAILABLE`; it is not an
invitation to choose a default window, attach to an arbitrary terminal, or widen
permissions. Deterministic provider fixtures exercise the shared control API.

## Pause, denial and recovery

Pause is durable and can be set by a separate CLI process without credentials or
enrollment. It blocks new intake, approval, execution, publication transitions
and new handoff admission. A handler already running can finish and save its
result. Status reports those running requests rather than pretending they were
undone. Network operations already in flight need reconciliation by the future
composition layer; the control CLI does not claim it can retract a publication.
Resume changes pause state only, never grants consent. Denial/revocation is
idempotent and durable for unstarted requests. Already-started work reports
`TOO_LATE`, requiring pause and reconciliation rather than a false undo.

Journal schema v2 adds local authority/approval records and the shared pause bit.
The v1-to-v2 migration is transactional and preserves existing requests, results,
collision records and replay tombstones. Old entries do not acquire invented
consent: the local control path rejects entries without authority bindings. Keep
a backup before a software update; do not open a v2 journal with older software
or delete state to clear a diagnostic. Backup/rollback packaging remains IL-024.

The enrollment fingerprint includes all local grants and destination fields,
even if an operator forgets to increment `revision`. Fingerprints and target
identity are checked again at confirmation and dispatch. Human prompts and
capability handlers run outside database writer transactions; only short state
changes hold the writer lock. Interrupted handlers retain crash ambiguity for
broker recovery instead of being advertised as safely completed failures.

Errors use stable codes, a remedy and an opaque local evidence alias. They do not
echo request text, raw arguments, credentials or personal input-file paths.
JSON output escapes control characters. This is not a guarantee that arbitrary
terminal evidence is safe to export: the existing evidence sanitizer is still
required before any publication.

## Manual Windows Terminal verification (not yet recorded)

Use a synthetic ping request, an explicit `confirm` grant and a short expiry.
Check help legibility, preview identity, Enter cancellation, rejection of a wrong
digest, correct approval without execution, denial/revocation after restart,
and pause/resume in a second terminal. Let one request expire while its prompt
is open and confirm that it cannot be approved. Use a spaced/Unicode local path.
Do not capture or attach private desktop content for this test. Hosted Windows
unit tests are separate from this interactive accessibility check.
