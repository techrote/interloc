# Interloc continuation handover — 2026-09-13

## Outcome and authority

Continuation of the user's `interloc DEV` work in `techrote/interloc`. The user
authorized implementation and PR merges after automated checks pass. Source
intake was `dac0854599d85f2809566099478874ede904a287`; the completed implementation
baseline is now `ba27ecf3ddf7d1e56bc4db8e9e63227fe70a14c5` after PRs #44/#45.
This handover is a documentation/status reconciliation on top of that baseline,
not another runtime implementation or a release qualification.

Two issues were newly implemented and merged; a previously merged transport
issue was reconciled and closed. A third implementation is preserved on an
unmerged branch. No source from that branch was included in either successful
implementation PR. Canonical task IDs, dependencies and positive gates remain
unchanged. No changes were made to the external Ansible/intrallm repositories.

## Completed and merged work

| Task | Scope | Publication and verification |
|---|---|---|
| IL-009 / #10 | Bounded local retention, pending-evidence protection, file-intent crash recovery, replay-preserving incident recovery | [PR #44](https://github.com/techrote/interloc/pull/44); merge `67156796416b57fa232cfe1130b9eef384e9a232`; final head `b1334d593f11e91db168b94b46c4d05a8f6798cd`; [CI 34732889624](https://github.com/techrote/interloc/actions/runs/34732889624) passed before merge; issue closed |
| IL-018 / #26 | Read-only pinned Ansible status/result telemetry, checkpoints, provenance and sanitized bounded evidence | [PR #45](https://github.com/techrote/interloc/pull/45); merge `ba27ecf3ddf7d1e56bc4db8e9e63227fe70a14c5`; final head `58ceef60b1c7651b5ee7bead8b77c5c4e8749c47`; [CI 34733379747](https://github.com/techrote/interloc/actions/runs/34733379747) passed before merge; issue closed |
| IL-005 / #6 | Post-merge reconciliation of existing transport, not new implementation | Existing [PR #43](https://github.com/techrote/interloc/pull/43), merge `dac0854599d85f2809566099478874ede904a287`; final head `20d4f7916504d3632212911d7f5a9619b76a61bc` and [CI 34729883333](https://github.com/techrote/interloc/actions/runs/34729883333) verified; stale open issue closed with live-smoke limitation preserved |

Retention's 40 new tests include tiny capacity, actual SQLite page-limit failure,
simulated ENOSPC, pending publication, reviewed expiry, interrupted write/delete,
unknown-file ownership, links and restart/replay cases. It never automatically
purges a replay ID, unknown file or Git history to make room. Its aggregate
quotas apply to owned payload/catalog storage, not unrelated filesystem data.

Telemetry's 32 new tests cover the actual `ansible.execution.v1` per-job format,
not an assumed generic stream. The producer source is pinned at
`techrote/ansible@ab1f9023545539f334ebc23635b54c8b03eac40d`. Slot/generation are
operator mappings or unavailable because the source files lack those fields.
Every reported outcome remains source-reported; `evidence_verified` is always
false. Evidence must be retained before accepting the proposed checkpoint.

Runbooks: [RETENTION_RECOVERY](RETENTION_RECOVERY.md) and
[ANSIBLE_TELEMETRY](ANSIBLE_TELEMETRY.md). Evidence:
[IL-009](evidence/IL-009.md), [IL-009-CI](evidence/IL-009-CI.md),
[IL-018](evidence/IL-018.md), [IL-018-CI](evidence/IL-018-CI.md) and
[existing IL-005](evidence/IL-005.md).

## Preserved unmerged IL-008 implementation

Branch: `work/il-008-completion-20260913`, head
`7973c0c6ecc57831477fa6d98ec4b08bde773934`. Code commit:
`474dea04fbb9e981becb2fdbc364155e844c4a6e`; exact code tree:
`bcdf9adb76d190be1a9a744038ede32850acb0f1`.

The branch implements strict local enrollment/import, request preview, interactive
full-digest approval, denial/revocation, durable pause/resume and status. It also
repairs broker transaction boundaries, adds schema-v2 authority/pause state,
checks current authority and expiry at dispatch, and tests migration/collision/
queue pressure/interrupt behavior. Those repairs are **not yet on main**; do not
compose a real courier around the old broker and claim the fixes are present.

The branch passes 149 local tests (111 intake baseline plus 38 new regressions).
Code [CI 34732065382](https://github.com/techrote/interloc/actions/runs/34732065382)
and docs-head [CI 34732138366](https://github.com/techrote/interloc/actions/runs/34732138366)
completed successfully. Branch-only guides are
[LOCAL_CONTROLS](https://github.com/techrote/interloc/blob/7973c0c6ecc57831477fa6d98ec4b08bde773934/docs/LOCAL_CONTROLS.md)
and [IL-008 evidence](https://github.com/techrote/interloc/blob/7973c0c6ecc57831477fa6d98ec4b08bde773934/docs/evidence/IL-008.md).

The attempted draft PR creation was blocked by the connector's safety checks
without an explanation. It was not retried through another route. **Do not
repackage this branch into another PR, direct-write it to main or otherwise
bypass that block.** Preserve the branch and recorded facts. Issue #9 remains
open; no IL-008 merge or downstream readiness is claimed. The interactive Windows
Terminal accessibility check is also NOT RUN. Hosted Windows unit tests do not
replace it. The branch has no default real capture/terminal execution provider.

`main` therefore retains help/doctor only. Neither retention nor telemetry adds
a user-facing execution loop or automatic publication service. This distinction
is intentional and must survive any README or status rewrite.

## Verification and reproducibility

The merged implementation has 183 passing local tests: 111 intake tests plus
40 retention and 32 telemetry tests. The additional 38 IL-008 tests are separate;
do not add its 149-test total to 183 or claim all 110 new tests are merged.
The implementation PRs passed the six-job Windows/Ubuntu Python 3.12/3.13/3.14
matrix. Checks include installation, full tests, CLI smoke and workflow audit.

Local environment: Linux, CPython 3.13.5, Git 2.47.3. These commands ran separately
from the source worktree (or use a clean installed development environment):

```text
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -v
python tools/audit_workflow.py
PYTHONPATH=src python -m interloc --help
PYTHONPATH=src python -m interloc doctor --json
```

The first line's environment-variable syntax is POSIX. For Windows, use the
installed environment from [DEVELOPMENT](DEVELOPMENT.md) and run
`python -W error::ResourceWarning -m unittest discover -s tests -v` separately.
Local retained test logs use opaque aliases rather than published private data.
No complete live REST issue snapshot was supplied to the offline auditor in this
pass; its zero graph/link errors are not a claim of a full live metadata audit.
Canonical issue/PR/check state was fetched separately for the changes reconciled.

Source publication used GitHub-created trees/commits with actual remote parent
SHAs. Each implementation tree was compared with its locally tested Git tree
before advancing the branch. Local worktrees used synthetic bookkeeping commits;
those local commit ancestors must not be confused with published remote history.
No source-archive authentication material or runtime files were committed.

The main entry-point smoke and doctor are not credential, live-provider or user
workflow tests. The pinned install for IL-008 was unavailable locally offline;
its evidence records that failure separately from a local non-isolated wheel
smoke. Hosted clean installation passed; do not relabel the local offline failure.

## Next work and preserved gates

IL-016 / [#17](https://github.com/techrote/interloc/issues/17) is the next independent
coding issue. It was assessed only, not implemented. Use the existing frozen
protocol/policy/evidence contracts, coordinate the schema lock, and add explicit
project/worktree grants rather than reusing the runtime mailbox grant. Its fixed
capabilities are defined in [INTEGRATIONS](INTEGRATIONS.md). Intake concerns:

- The existing transport's `subprocess.run(capture_output=True)` is not a
  during-capture output bound. A read adapter needs bounded pipe handling and
  qualified cancellation/timeouts rather than clipping after allocation.
- Git inspection must avoid index writes and external helpers. The primary
  [git-status manual](https://git-scm.com/docs/git-status) documents optional index
  refresh and `--no-optional-locks`. The [git-config manual](https://git-scm.com/docs/git-config)
  documents filesystem-monitor hooks and version-dependent boolean interpretation;
  a flag recipe must be qualified against the supported Git versions.
- The [gh api manual](https://cli.github.com/manual/gh_api) describes method changes
  from fields, file-reading field syntax and endpoint placeholders. The new adapter
  must construct fixed GET operations from typed values, never expose these
  features as requester-provided argv, endpoints or input-file selectors.

These are implementation review findings, not passing tests or authorization to
widen the capability set. No IL-016 evidence-completion file or PR was created.

IL-012 can continue fixture/research work independently, but actual capture
feasibility requires an enrolled interactive Windows desktop. IL-010 and IL-019
remain blocked by IL-008. Manual handoff, packaging, resource/security and full
release work follow their unchanged prerequisites. Overview #1 remains open.
[IL-020 evidence](evidence/IL-020.md) retains G-assisted NEGATIVE / NOT QUALIFIED;
IL-022 cannot begin on the strength of the research issue being closed.

Live disposable-mailbox smoke, live local Ansible smoke, Windows Terminal manual
accessibility, native capture, ordinary-Chat retrieval/image delivery, hardware
power-cut behavior and independent resource/security/release qualification are
NOT RUN or incomplete as stated in the individual reports. No source-repository
writes, hosted CI or synthetic data were substituted for those live checks.
