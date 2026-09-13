# IL-016 continuation / execution ledger supplement

Date: 2026-09-13. Read this after [the previous handover](SESSION_HANDOVER_2026-09-13.md)
and [the implementation ledger](EXECUTION_LEDGER.md). Those files preserve the
previous run's state; this supplement updates the IL-016 frontier without
rewriting its history or importing blocked work.

## Work performed

PR [#47](https://github.com/techrote/interloc/pull/47) implements the independently
ready IL-016 / issue #17 from base
`3ccd63dcea215e4dbf40b7d08a6df8811133cd35`. The read-only capability library,
coordinated schema/policy extension and 68 new tests are isolated from IL-008.
See [component instructions](READ_ADAPTERS.md) and [retained evidence](evidence/IL-016.md).
Use the PR's actual merge state and final checks, not this sentence, to determine
whether these artifacts have reached main.

The original 183-test main baseline is preserved. The combined suite discovers
251 tests, including platform-specific skips. No workflow, dependency lock,
broker, CLI, retention or telemetry implementation was changed by IL-016.

Two Windows linked-worktree failures were investigated before merge. The first
was a fixture decoding Git's UTF-8 pointer using the Windows default code page.
The second exposed a production identity check that compared path spelling rather
than filesystem identity. The correction compares stat identities only after
rejecting links/junctions and non-directories. Three additional regressions cover
different directories/files, links, and Windows long-name aliases. No enrollment
restriction or failing assertion was removed. The full failure and retest record
is retained in the evidence report.

## Dependency frontier

IL-016 is an independently reviewed component, not runtime wiring. Its merge
satisfies only the read-adapter prerequisite of IL-017. IL-017/#18 still requires
merged IL-008. IL-010/#11 and IL-019/#27 remain blocked by that same missing
approval/control integration. Do not cherry-pick or disguise the IL-008 changes
as a read-adapter or documentation follow-up.

IL-008/#9 remains on `work/il-008-completion-20260913`, last recorded head
`7973c0c6ecc57831477fa6d98ec4b08bde773934`; its publication block and outstanding
interactive terminal check are preserved. Its tested code is not part of this
branch. Current executable CLI commands remain help/doctor only.

IL-012/#13 is the remaining independent native-feasibility lane. It needs actual
interactive Windows capture evidence; a hosted Windows process/Git test is not a
capture qualification. Native capture, private vision delivery, ordinary-Chat,
text integration and the full release remain unqualified. G-assisted remains
negative; do not implement IL-022/#31. The overview issue #1 stays open.

## Repository and publication boundary

The source repository ID remains 1367477815. GitHub reported its visibility as
public during this continuation. No visibility mutation was performed here.
This source repository is not enrolled as a runtime mailbox. No user logs,
screenshots, credentials or runtime journals were published; only code,
synthetic tests and bounded implementation documentation belong in this change.
A runtime mailbox must still be separately enrolled and private.

A proposed CI workflow edit was blocked by the connector. It was not retried or
included through another API. The existing workflow remains byte-for-byte
unchanged. PR #47 is independent source work authorized by issue #17, not a
workaround for that workflow block or the IL-008 PR block.

## Continuation rules

Check actual main/PR/issue state before assigning another lane. Retain a single
writer for shared schemas, policy contracts and workflow.json. Issue closure
requires merged evidence and final passing checks. Live gh smoke remains a
separate, explicitly enrolled read-only exercise; this run's connector access
and fake API responses are not live installed-adapter evidence.
