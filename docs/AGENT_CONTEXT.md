# Agent context / retrieval bundle

This is a small source-of-truth index rather than duplicated prompts. Read the current file versions at your checked-out base SHA; record it in your evidence. External links in RESEARCH are mutable and must be rechecked for gated work.

## Current continuation entry

Read [SESSION_HANDOVER_2026-09-13](SESSION_HANDOVER_2026-09-13.md) and the current [execution ledger](EXECUTION_LEDGER.md) before assigning work. The foundation and several core components, retention (IL-009), telemetry (IL-018), and workflow tooling are merged. Do not repeat the initial bootstrap merely because the offline auditor's empty-completion example lists IL-001.

IL-008's tested approval/control branch is **not merged**: its PR creation was blocked and must not be retried through another route. `main` retains help/doctor only. IL-010 and IL-019 therefore remain blocked. IL-016 is the next independent coding lane. Interactive Windows, live mailbox, ordinary-Chat, visual and release checks remain separate from successful hosted tests. Consult the retained negative IL-020 gate before any assisted-transport work.

## Universal reading

Read root AGENTS.md, README, your GitHub issue, ROADMAP, SPEC, SECURITY and VERIFY. Use workflow.json for stable IL identifiers, issue mappings and direct dependencies. Consult PROVENANCE when interpreting a suggestion as a requirement. DECISIONS distinguishes selected architecture from unresolved options.

## Focused routes

| Workstream | Additional context |
|---|---|
| Schema, journal, policy | SPEC request/response/state sections; SECURITY authority; VERIFY T-contract/T-state/T-policy |
| GitHub transport, privacy, logs | SPEC mailbox/budgets/session collection; RESEARCH S4-S6/S8; VERIFY T-transport/T-collect/T-retention; [RETENTION_RECOVERY](RETENTION_RECOVERY.md) |
| Screenshots | RESEARCH G-capture/G-vision; SECURITY capture; VERIFY T-capture/T-vision |
| GitHub CLI / Ansible | INTEGRATIONS; [ANSIBLE_TELEMETRY](ANSIBLE_TELEMETRY.md); RESEARCH S6/S11/S12; recovered-context limitation in PROVENANCE |
| Chat/browser/TUI | RESEARCH G-chat/G-browser/G-assisted; SPEC UI; never equate local status TUI with ordinary-Chat CLI |
| Release / workflow tooling | ROADMAP, VERIFY, EXECUTION_LEDGER, workflow.json; inspect actual issues/PRs before changing them |

## Implementation prompt template

Implement the issue with stable ID IL-xxx in techrote/interloc. Read its dependencies and canonical context first. Confirm merged prerequisite evidence and conditional gates. Work in an isolated branch/worktree from a recorded base SHA. Own only the listed paths; coordinate shared interfaces and dependency/lockfile changes. Implement the smallest complete scoped behavior with production error handling and bounded resource use. Add automated positive and adversarial tests plus documented native/external checks. Do not widen permissions, bypass mode/account constraints or claim mock tests as live proof. Retain docs/evidence/IL-xxx.md with exact commands, outcomes, tested SHA, limitations and artifact links. Reconcile the issue and manifest; stop only the affected operation on a blocker and continue independent scoped work.

## Evidence report template

Use headings: Objective; Inputs/base SHA; Implemented scope; Non-goals; Changed files; Environment; Commands/results; Native/external checks; Security review; Gate decisions; Remaining blockers; Reproduction. State PASS/FAIL/NOT RUN for each required test. Do not place real user logs/images/credentials in the report. Record a sanitized artifact descriptor or local evidence path alias instead.

## Scheduling rule

Dependency readiness and path-lock compatibility are both necessary. An issue marked open can still be ready; a closed issue without passing merged evidence is not enough. Optional gated tasks are not part of MVP critical-path completion. Do not edit an existing nonempty issue solely to make the local template look uniform; preserve useful human/agent findings.
