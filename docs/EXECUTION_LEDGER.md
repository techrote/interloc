# Planning and implementation execution ledger

Planning baseline: 2026-09-12 / interloc-plan-v1.2. Runtime reconciliation: 2026-09-13, after merge `ba27ecf3ddf7d1e56bc4db8e9e63227fe70a14c5`.

## Current runtime status

The table records merged component scope, not end-to-end or release qualification. Exact commands, tested SHAs and limitations remain in each evidence report. The source tree still has the original help/doctor CLI; the newer controls are only on the unmerged IL-008 branch.

| Stable tasks | Canonical issues | Current state | Evidence / result |
|---|---|---|---|
| IL-001..IL-007 | #2..#8 | Merged; closed/completed | `docs/evidence/IL-001.md` through `IL-007.md`; IL-005 post-merge closure reconciled in this pass |
| IL-008 | #9 | Implemented/tested branch; OPEN, not merged | 149 local tests; push CI passed; PR creation blocked; interactive Windows Terminal check NOT RUN |
| IL-009 | #10 | Merged; closed/completed | PR #44, merge `67156796416b57fa232cfe1130b9eef384e9a232`; 40 new tests; [evidence](evidence/IL-009.md), [CI](evidence/IL-009-CI.md) |
| IL-018 | #26 | Merged; closed/completed | PR #45, merge `ba27ecf3ddf7d1e56bc4db8e9e63227fe70a14c5`; 32 new tests; [evidence](evidence/IL-018.md), [CI](evidence/IL-018-CI.md) |
| IL-020 | #30 | Research complete; closed/completed | [Evidence](evidence/IL-020.md); G-assisted NEGATIVE / NOT QUALIFIED, not permission for IL-022 |
| IL-028 | #25 | Tooling merged; closed/completed | [Evidence](evidence/IL-028.md); ongoing maintenance does not reopen release gates |

IL-005 closure reconciles existing PR #43, not a new transport implementation. The final PR head `20d4f7916504d3632212911d7f5a9619b76a61bc` passed CI `34729883333`; its merge `dac0854599d85f2809566099478874ede904a287` predates this continuation. The live disposable-mailbox smoke stays NOT RUN.

The combined merged implementation passes 183 local tests. The 38 additional IL-008 tests are on its separate 149-test branch and are **not** part of the main suite. See the [session handover](SESSION_HANDOVER_2026-09-13.md) for exact branches, check receipts, next work and preserved limitations.

## Current execution frontier

IL-016/#17 is independent and ready for implementation from merged evidence. Its intake review identified bounded subprocess capture and non-executing Git configuration as requirements; no IL-016 implementation or qualification is claimed. IL-012/#13 has satisfied code prerequisites but native visual verification needs a separately available interactive Windows desktop. IL-008's publication block applies only to that branch; it must not be bypassed by another PR or direct main write.

IL-010/#11 and IL-019/#27 remain blocked by IL-008. Later manual handoff, packaging, live Chat/vision, resource/security and release gates remain unfinished. IL-022/#31 additionally has a negative G-assisted decision. Overview #1 remains open.

The task manifest records publication, identity, paths and dependency/gate structure. It is not a per-task runtime status database. Its descriptive runtime state is now `partial_components`; the issue/evidence records above supply execution status. No task IDs, dependency edges, positive-gate requirements or historical duplicate mappings were changed.

## Historical planning scope

The remainder preserves the original planning/publication receipt. References to that pass describe its historical scope, not the current implementation state.

## Initial inspection and preservation

`techrote/interloc` began private and empty on `main`; all-state issue/PR listings initially returned zero entries. No pre-existing source work was overwritten. The isolated `zaaggenz` target reference was reconciled as a copy/paste error; only `interloc` was changed.

Bootstrap README commit: `35e0179806db967d7f94ebfb7c643632be4d5d33`. Canonical architecture commit: `d5f35b196e56c601ae26be8e126fe53334e999b1`. Original audited workflow publication commit: `74dc5d43d291f53771ce7e7932c04f26c1bcb71a`.

## Stage ledger

| Stage | Status | Outcome |
|---|---|---|
| 1 Conversation reconciliation | Complete with recorded source limitation | PROVENANCE / INTEGRATIONS |
| 2 Implementation-readiness decision | Complete | Core decomposed; conditional work gated |
| 3 Repair omissions/assumptions | Complete | Decisions, research gates and safety boundaries recorded |
| 4 Supporting context/specifications | Complete | SPEC, SECURITY, VERIFY, RESEARCH, INTEGRATIONS, AGENT_CONTEXT |
| 5 Milestone/dependency hierarchy | Complete | Five repository-native milestones; 28 stable task IDs |
| 6 Concurrency design | Complete | Atlas and manifest path/resource-lock model |
| 7 Initial repository/GitHub inspection | Complete | Empty repository/issues/PRs inspected before mutation |
| 8 Canonical documentation | Complete | Published on `main` |
| 9 GitHub issue publication | **Complete** | Every IL-001..IL-028 task has a canonical issue |
| 10 First issue-set audit | Complete | Original published set audited |
| 11 Corrections / second audit | Complete | Original planning audit retained in `docs/evidence/planning-audit.md` |
| 12 Final consistency publication | Complete | Publication mappings reconciled in README, ROADMAP, manifest and overview |

## Final issue publication mapping

IL-001..IL-017 remain issues #2..#18 except for the task-number/issue-number divergence already shown in the atlas. IL-021 is #19, IL-023 #20, IL-024 #21, IL-025 #22, IL-026 #23, IL-027 #24, and IL-028 #25.

The previously missing publications are now resolved:

- IL-013: issue #14 is now the implementation issue.
- IL-018: canonical issue #26.
- IL-019: canonical issue #27.
- IL-020: issue #30.
- IL-022: issue #31.

Issues #28 and #29 were created during the publication-completion pass before the earlier #26/#27 publications surfaced in repository search. They were immediately closed with GitHub state reason `duplicate`; they are not canonical work items.

## Publication-only reconciliation

At the user's explicit request, this pass did not reassess or redesign the plan. It only completed missing issue publication and mechanically reconciled stale publication-state references. The existing requirements, dependencies and implementation gates were preserved. Conditional IL-022 remains gated by the positive decision defined by IL-020; issue publication is not implementation authorization when a gate is unsatisfied.

README, ROADMAP, `docs/workflow.json`, publication-history documentation, overview #1, relevant dependent issue text and workflow-test expectations were updated to stop advertising obsolete publication blockers.

## Historical verification

The original planning audit ran on Linux/Python 3.13.5: graph validation returned zero errors and 20 offline auditor tests passed. That audit is retained in [planning-audit.md](evidence/planning-audit.md). The publication-completion pass intentionally did not redo that audit; it changed publication metadata/mappings rather than the underlying architecture.

## Historical remaining work at planning completion

At planning completion, the remaining blockers were implementation/runtime-specific rather than missing issue publications: native Windows verification, mailbox enrollment, ordinary-Chat compatibility evidence, image-delivery qualification, external integration availability, conditional transport gating, packaging, resilience/security qualification and release evidence.

Native GitHub milestone objects remain unavailable through the currently exposed connector operations; the milestone hierarchy remains repository-native. No secret credentials, real terminal output or private screenshots were collected during publication.
