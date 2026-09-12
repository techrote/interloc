# Execution atlas and milestone hierarchy

Plan v1.2, 2026-09-12. Stable IL IDs are not GitHub issue numbers. All planned task IDs are now published; publication history is retained in [BLOCKERS](BLOCKERS.md). Runtime dependencies and conditional gates remain unchanged.

## Milestones

| ID | Outcome | Tasks | Exit condition |
|---|---|---|---|
| M0 | Contracts and safety foundation | IL-001..004, IL-020, IL-028 | Offline contracts, local authority/state and documented transport decision. |
| M1 | Supervised text courier | IL-005..011, IL-021 | Enrolled output, sanitized immutable evidence and qualified human handoff. |
| M2 | Supervised visual MVP and release | IL-012..015, IL-024..027 | Native visual gates plus installation, security and reliability evidence. |
| M3 | Separately qualified integrations and local UI | IL-016..019, IL-023 | Each extension independently qualified or disabled; optional work is not a core release dependency. |
| M4 | Conditional transport | IL-022 | May execute only after its documented positive gate. |

Milestones describe outcomes, not deadlines or strictly serialized phases. They remain repository-native because this connector does not expose native milestone creation.

## Issue and dependency atlas

| Stable task | GitHub issue | Deliverable | Direct prerequisites |
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
| IL-012 | [#13](https://github.com/techrote/interloc/issues/13) | Windows visual-capture feasibility evidence | IL-001, IL-003 |
| IL-013 | [#14](https://github.com/techrote/interloc/issues/14) | Scoped visual-capture provider | IL-004, IL-007, IL-012 |
| IL-014 | [#15](https://github.com/techrote/interloc/issues/15) | Image delivery qualification | IL-010, IL-013 |
| IL-015 | [#16](https://github.com/techrote/interloc/issues/16) | Supervised visual integration qualification | IL-008, IL-011, IL-013, IL-014 |
| IL-016 | [#17](https://github.com/techrote/interloc/issues/17) | Read-only GitHub/worktree adapters | IL-004, IL-005, IL-007 |
| IL-017 | [#18](https://github.com/techrote/interloc/issues/18) | Optional confirmed GitHub issue writes | IL-008, IL-016 |
| IL-018 | [#26](https://github.com/techrote/interloc/issues/26) | Read local orchestration status/result data | IL-006, IL-007 |
| IL-019 | [#27](https://github.com/techrote/interloc/issues/27) | Optional validated task-proposal handoff | IL-008, IL-018 |
| IL-020 | [#30](https://github.com/techrote/interloc/issues/30) | Ordinary-Chat handoff/minimal-client research | IL-001 |
| IL-021 | [#19](https://github.com/techrote/interloc/issues/19) | Manual evidence and attachment handoff | IL-008, IL-010 |
| IL-022 | [#31](https://github.com/techrote/interloc/issues/31) | Conditional assisted ordinary-Chat transport | IL-011, IL-020, IL-021 plus positive G-assisted gate |
| IL-023 | [#20](https://github.com/techrote/interloc/issues/20) | Optional local Interloc status TUI | IL-008, IL-021 |
| IL-024 | [#21](https://github.com/techrote/interloc/issues/21) | Windows text-baseline packaging | IL-010, IL-021 |
| IL-025 | [#22](https://github.com/techrote/interloc/issues/22) | Independent text resilience/resource qualification | IL-010, IL-021, IL-024 |
| IL-026 | [#23](https://github.com/techrote/interloc/issues/23) | Independent text security qualification | IL-010, IL-021, IL-024 |
| IL-027 | [#24](https://github.com/techrote/interloc/issues/24) | Full supervised MVP release qualification | IL-011, IL-015, IL-025, IL-026 |
| IL-028 | [#25](https://github.com/techrote/interloc/issues/25) | Workflow audits and metadata maintenance | IL-001 |

Issues #28 and #29 are closed duplicates of canonical IL-018/#26 and IL-019/#27 and must not be assigned.

## Execution order and safe concurrency

Begin IL-001 (#2). Once its evidence is merged, IL-002 (#3), IL-020 (#30), and IL-028 (#25) may proceed subject to their own resource/path locks. After IL-002, policy IL-003 and collection IL-006 are independent. After policy, broker IL-004, transport IL-005, evidence IL-007 and capture-feasibility IL-012 can proceed subject to declared locks.

The manual text path remains IL-005/006/007/008/009 -> IL-010 -> IL-011 and IL-021. Packaging follows IL-024; IL-025 and IL-026 can run beside one another. Full release IL-027 still requires actual visual acceptance IL-015.

M3 integrations are separately qualified. IL-018 is canonical #26 and IL-019 is canonical #27. IL-020 research may complete with a negative result; IL-022 #31 remains published but is not implementation-ready without a positive G-assisted decision.

Every assignment requires an isolated branch/worktree, base SHA, stable ID, owned paths and test/evidence plan. A prerequisite is satisfied by merged artifacts and passing required evidence, not merely issue closure. Serialize shared schemas, dependency lockfiles, runtime composition, state migrations, release metadata, mailbox writes and live tests on the same desktop/conversation/object.

## Stopping and evidence

Missing Windows access, mailbox credentials, Chat access, external integration files, or a positive conditional gate blocks only relevant live work. Mark NOT RUN rather than PASS. A negative compatibility result must not trigger private endpoint use, wider privileges, public artifact hosting or a silent product-mode switch.

Read all-state issues/PRs and the current canonical mapping before a planning rerun. Match unique stable markers and preserve useful findings. Run `python tools/audit_workflow.py` and `python -m unittest discover -s tests/workflow -v` separately. Runtime and release evidence follows [VERIFY](VERIFY.md); publication completion is recorded in [EXECUTION_LEDGER](EXECUTION_LEDGER.md).
