# Execution atlas and milestone hierarchy

Plan v1, 2026-09-12. This atlas is canonical with [workflow.json](workflow.json). Issue numbers are bound there after creation; stable IL IDs must not be recycled. See [AGENT_CONTEXT](AGENT_CONTEXT.md) for the reusable prompt and [VERIFY](VERIFY.md) for evidence gates.

## Milestones

| ID | Outcome | Tasks | Exit condition |
|---|---|---|---|
| M0 | Contracts, safety foundation and feasibility decisions | IL-001..004, IL-020, IL-028 | Offline validators, local authority, durable state and explicit Chat-transport decision; workflow tooling may continue independently. |
| M1 | Supervised text courier | IL-005..011, IL-021 | Enrolled output -> sanitized immutable evidence -> explicit human pointer -> real ordinary-Chat compatibility report. |
| M2 | Supervised visual MVP and release | IL-012..015, IL-024..027 | Scoped capture, honest image delivery, installation, resilience/security evidence and supervised release qualification. |
| M3 | Cross-project integrations and local control UI | IL-016..019, IL-023 | Qualified read adapters; optional writes/proposals/UI remain separately enabled and reviewed. |
| M4 | Conditional assisted browser transport | IL-022 | Only a positively permitted/supported route may ship; a blocked optional route does not block M2. |

Milestones express outcomes, not calendar promises or strictly sequential phases. M3 read adapters and M0 research can run beside core work. Native GitHub milestones/labels are convenience metadata; the current connector does not expose creation for them and its milestone read endpoint was rejected. IL-028 provides idempotent gh synchronization; no native milestone creation is claimed by the planning run.

## Task atlas

| Stable ID | Deliverable | Direct prerequisites | Scope lane |
|---|---|---|---|
| IL-001 | Python package/test/CI and Windows development foundation | none | foundation |
| IL-002 | Strict protocol schemas and conformance vectors | 001 | protocol |
| IL-003 | Local policy/enrollment/approval decision model | 002 | policy |
| IL-004 | SQLite dispatcher, replay/crash/cancel semantics | 002,003 | broker |
| IL-005 | gh mailbox transport and transactional publication | 002,003 | transport |
| IL-006 | Enrolled session collector and worktree attribution | 002 | collectors |
| IL-007 | Redaction, chunking, artifact/index model | 002,003 | evidence |
| IL-008 | CLI local approvals, import, pause and diagnostics | 004 | cli |
| IL-009 | Bounded retention, disk/backpressure and privacy recovery | 004,005,007 | retention |
| IL-010 | End-to-end supervised text courier slice | 005,006,007,008,009 | text integration |
| IL-011 | Actual ordinary-Chat connector/mode compatibility gate | 010 | external evidence |
| IL-012 | Real Windows capture backend feasibility | 001,003 | capture research |
| IL-013 | Scoped capture/window identity provider | 004,007,012 | capture implementation |
| IL-014 | Private image/model-vision route proof or manual fallback | 010,013 | vision evidence |
| IL-015 | Screenshot RPC and consent/delivery integration | 008,011,013,014 | visual integration |
| IL-016 | Read-only gh and mapped worktree adapters | 004,005,007 | github capabilities |
| IL-017 | Optional previewed/confirmed GitHub issue writes | 008,016 | github writes |
| IL-018 | Ansible status and pinned-reference adapter | 006,007 | ansible read |
| IL-019 | Optional fixed-task proposal/human-launch handoff | 008,018 | ansible proposals |
| IL-020 | Supported Chat transport/terms/minimal-client research | 001 | browser decision |
| IL-021 | Human-mediated notifier/attachment handoff queue | 008,010,020 | notify |
| IL-022 | Conditional assisted-browser adapter | 011,020,021 plus positive G-assisted | optional browser |
| IL-023 | Optional lightweight Interloc control/status TUI | 008,021 | tui |
| IL-024 | Windows packaging/startup/update/rollback | 010,021 | distribution |
| IL-025 | Independent fault injection and resource/performance soak | 015,021,024 | reliability |
| IL-026 | Independent adversarial security/privacy verification | 015,021,024 | security audit |
| IL-027 | Supervised MVP qualification and operator handoff | 011,015,025,026 | release |
| IL-028 | Workflow audit/reconciliation and optional native metadata sync | 001 | workflow tools |

## Safe concurrency

A ready task needs all prerequisites merged with passing required evidence, no unresolved positive gate, and no overlapping path lock. Use distinct branches/worktrees. Test fixtures/interfaces produced by prerequisites are consumed immutably; proposed changes go back to the owning lane. Shared files (pyproject/lockfile, schemas, runtime wiring, roadmap/workflow manifest) have one integrator at a time. Do not run multiple agents in the same worktree.

Illustrative waves, not mandatory batches: IL-001; then IL-002/020/028; then IL-003/006; then IL-004/005/007/012; then IL-008/009/013/016/018 when individually ready; then IL-010/017/019; then IL-011/014/021; then IL-015/024/023 and positively gated IL-022; then IL-025 and IL-026 on separate test/report paths; finally IL-027. Some paths can advance without waiting for unrelated items in a wave.

Serialized operations: schema changes; dependency/lockfile changes; one writer per outbox/device; local journal ownership; release metadata; issue/manifest reconciliation; live write tests to the same object; native capture/consent tests on the same desktop. IL-017 and IL-019 must not be tested against active project tasks without explicit local approval. No plan item grants permission to interfere with running CyberSand/ZaagGen worktrees.

## Stop and blocker handling

Stop the affected operation on security/permission ambiguity, an unsupported Chat/vision route, missing native platform, API authentication failure, ref rewind, mutable dependency evidence or unexpected existing work. Record blocker, dependent IDs and unaffected ready tasks. Continue offline and independent lanes. A failed automatic-browser gate blocks IL-022 only, not IL-021/manual handoff. Missing Ansible executables block live IL-018/019 integration only. Missing Windows access blocks native capture/release evidence but not core unit tests.

Do not close the overview on the strength of planning completeness. Planning completion and runtime release completion are separate. The MVP requires IL-027 evidence; optional M3/M4 tasks must be clearly enabled, disabled or deferred.

## Repeatable orchestration

Before a rerun, read all issues/PRs including closed items and compare IL markers, docs and manifest. Match by stable ID, not title alone. Update existing artifacts, preserve useful findings, and refuse ambiguous duplicate matches. Newly discovered work gets a new stable ID with dependency/path/verification mapping. Record changes in EXECUTION_LEDGER. Native metadata sync is dry-run by default and must not delete existing issues/milestones/labels.
