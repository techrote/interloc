# Execution atlas and milestone hierarchy

Plan v1.1, 2026-09-12. [workflow.json](workflow.json) is the machine-readable map. Stable IL IDs are not GitHub issue numbers. [BLOCKERS](BLOCKERS.md) overrides readiness for restricted work; rejected issue bodies are not duplicated here.

## Milestones

| ID | Outcome | Tasks | Exit condition |
|---|---|---|---|
| M0 | Contracts and safety foundation | IL-001..004, IL-020, IL-028 | Offline contracts/local authority/state; transport research has a separate external blocker and is not required for manual handoff. |
| M1 | Supervised text courier | IL-005..011, IL-021 | Enrolled output, sanitized immutable evidence and qualified human handoff. |
| M2 | Supervised visual MVP and release | IL-012..015, IL-024..027 | Native visual gates plus installation/security/reliability evidence; currently blocked for full release. |
| M3 | Separately qualified integrations and local UI | IL-016..019, IL-023 | Each extension independently qualified or disabled; optional work is not a core release dependency. |
| M4 | Conditional transport | IL-022 | Deferred; no implementation authority or positive gate is inferred. |

Milestones describe outcomes, not deadlines or strictly serialized phases. They exist in this repository, not as native GitHub milestone objects: the connector did not expose the necessary administrative operation. IL-028 may later maintain optional metadata without recreating blocked issues.

## Issue and dependency atlas

All published task issues include objective, scope/non-goals, prerequisite/concurrency guidance, canonical context, implementation prompt, acceptance criteria, verification, expected artifacts and stopping conditions. Issue 14 is an administrative exception: its prompt forbids implementation until authorized resolution.

| Stable task | GitHub issue | Deliverable / disposition | Direct prerequisites |
|---|---|---|---|
| IL-001 | [#2](https://github.com/techrote/interloc/issues/2) | Development foundation | None |
| IL-002 | [#3](https://github.com/techrote/interloc/issues/3) | Protocol schemas and conformance | IL-001 |
| IL-003 | [#4](https://github.com/techrote/interloc/issues/4) | Local enrollment and policy | IL-002 |
| IL-004 | [#5](https://github.com/techrote/interloc/issues/5) | Durable broker state | IL-002, IL-003 |
| IL-005 | [#6](https://github.com/techrote/interloc/issues/6) | Private mailbox transport | IL-002, IL-003 |
| IL-006 | [#7](https://github.com/techrote/interloc/issues/7) | Enrolled session collection | IL-002 |
| IL-007 | [#8](https://github.com/techrote/interloc/issues/8) | Sanitized evidence and manifests | IL-002, IL-003 |
| IL-008 | [#9](https://github.com/techrote/interloc/issues/9) | Local approval and control CLI | IL-004 |
| IL-009 | [#10](https://github.com/techrote/interloc/issues/10) | Retention and privacy recovery | IL-004, IL-005, IL-007 |
| IL-010 | [#11](https://github.com/techrote/interloc/issues/11) | Supervised text integration | IL-005, IL-006, IL-007, IL-008, IL-009 |
| IL-011 | [#12](https://github.com/techrote/interloc/issues/12) | Ordinary-Chat compatibility evidence | IL-010 |
| IL-012 | [#13](https://github.com/techrote/interloc/issues/13) | Windows capture feasibility evidence | IL-001, IL-003 |
| IL-013 | [#14](https://github.com/techrote/interloc/issues/14) | Publication safeguard tracker; not an implementation assignment — blocked_tracker | IL-004, IL-007, IL-012 |
| IL-014 | [#15](https://github.com/techrote/interloc/issues/15) | Image delivery qualification | IL-010, IL-013 |
| IL-015 | [#16](https://github.com/techrote/interloc/issues/16) | Supervised visual integration qualification | IL-008, IL-011, IL-013, IL-014 |
| IL-016 | [#17](https://github.com/techrote/interloc/issues/17) | Read-only GitHub and worktree adapters | IL-004, IL-005, IL-007 |
| IL-017 | [#18](https://github.com/techrote/interloc/issues/18) | Optional confirmed GitHub issue writes | IL-008, IL-016 |
| IL-018 | **Unpublished** | Unpublished integration task; external safeguard — blocked_publication | IL-006, IL-007 |
| IL-019 | **Unpublished** | Deferred dependent integration — deferred_unpublished | IL-008, IL-018 |
| IL-020 | **Unpublished** | Unpublished transport research; external safeguard — blocked_publication | IL-001 |
| IL-021 | [#19](https://github.com/techrote/interloc/issues/19) | Manual evidence and attachment handoff | IL-008, IL-010 |
| IL-022 | **Unpublished** | Deferred conditional transport — deferred_unpublished | IL-011, IL-020, IL-021 |
| IL-023 | [#20](https://github.com/techrote/interloc/issues/20) | Optional local Interloc status TUI | IL-008, IL-021 |
| IL-024 | [#21](https://github.com/techrote/interloc/issues/21) | Windows text-baseline packaging | IL-010, IL-021 |
| IL-025 | [#22](https://github.com/techrote/interloc/issues/22) | Independent text resilience and resource qualification | IL-010, IL-021, IL-024 |
| IL-026 | [#23](https://github.com/techrote/interloc/issues/23) | Independent text security qualification | IL-010, IL-021, IL-024 |
| IL-027 | [#24](https://github.com/techrote/interloc/issues/24) | Full supervised MVP release qualification | IL-011, IL-015, IL-025, IL-026 |
| IL-028 | [#25](https://github.com/techrote/interloc/issues/25) | Workflow audits and optional metadata maintenance | IL-001 |

## Execution order and safe concurrency

Begin IL-001 (#2). Once its evidence is merged, IL-002 (#3) and IL-028 (#25) may proceed in separate worktrees. After IL-002, policy IL-003 and collection IL-006 are independent. After policy, broker IL-004, transport IL-005, evidence IL-007 and capture-feasibility IL-012 can proceed subject to path/resource locks. Each lane advances as its own prerequisites pass, not as a single mandatory batch.

The manual text path is IL-005/006/007/008/009 -> IL-010 -> IL-011 and IL-021. Text packaging follows IL-024; independent text qualification IL-025 and IL-026 can then run beside one another. They do not depend on the blocked native provider. Full release IL-027 still requires actual visual acceptance IL-015 and must not be called complete from a text-only preview.

Other published adapters/UI advance only when their declared prerequisites pass. IL-014 may prepare synthetic delivery research while its native acceptance remains blocked. This is permission for research already in its published scope, not an alternate implementation of IL-013. IL-018/019/020/022 are not assignable published work.

Every assignment needs: isolated branch/worktree, base SHA, stable ID, owned paths, and test/evidence plan. A dependency is satisfied by merged artifacts and passing required evidence, not merely issue closure. The manifest records path scopes, locks and gate requirements. `tools/audit_workflow.py` exposes `conflicts()` for candidate pairs; check dependency readiness separately.

Serialize shared schemas, dependency lockfiles, composition root, state migrations, workflow manifest, release metadata, mailbox writes and live tests on the same desktop/conversation/object. Path-prefix comparisons are case-insensitive for Windows. An issue may propose changes outside its owned paths only through coordination with the owner/integrator. Do not alter active CyberSand/ZaagGen worktrees.

Each task owns its docs/evidence/IL-xxx.md report in addition to listed implementation paths. Shared canonical specification edits need a reviewed decision and coordination, not parallel blind rewrites. Runtime wiring and registry/schema extension work must acquire the appropriate lock even when a new adapter file itself is disjoint.

## Blockers and stopping

B1 holds IL-013 and native dependent IL-014/015/full IL-027. B2 holds IL-018 and unpublished IL-019. B3 holds IL-020 and unpublished IL-022. Administrative closure or an issue number never clears these safeguards. See BLOCKERS for observed events and boundaries.

Missing Windows, approved mailbox credentials or access to the chosen Chat surface blocks only relevant live tests; offline and independent work continues. Mark NOT RUN, not PASS. A negative compatibility result must not trigger private endpoint use, wider privileges, public artifact hosting or a silent product-mode switch.

## Repeatability and evidence

Read all-state issues/PRs and the current manifest before a planning rerun. Match unique stable markers, preserve useful human/agent findings, and refuse ambiguous duplicates. Update rather than recreate. The readonly auditor validates the DAG, issue mapping, document inventory/links and execution sections when a full issue snapshot is supplied. IL-028 extends metadata reconciliation; it must not publish withheld content as a workaround.

Run `python tools/audit_workflow.py` and `python -m unittest discover -s tests/workflow -v` separately. Record exact commands and exit status. Gate and release evidence follows [VERIFY](VERIFY.md); source references and distinctions between requirements/design/findings remain in [PROVENANCE](PROVENANCE.md), [RESEARCH](RESEARCH.md) and [DECISIONS](DECISIONS.md).

Planning-document completion is separate from issue-publication completeness and runtime-release completeness. The [ledger](EXECUTION_LEDGER.md) records all three explicitly.
