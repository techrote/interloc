# Planning provenance and reconciliation

Baseline: 2026-09-12. Labels used here: **R** user requirement; **P** preference; **F** observed/researched fact; **D** selected design; **A** assumption to verify; **O** optional idea; **X** rejected or superseded assertion. Read [RESEARCH](RESEARCH.md) for source identifiers and [DECISIONS](DECISIONS.md) for decisions.

## Sources inspected

The complete planning exchange supplied in the current conversation was reviewed: desire for lightweight ordinary Chat; avoiding Work/Codex usage; terminal courier; repo-backed scratchpad/cache; two-way requests; name Interlocuator/interloc; screenshot example; and this repository-native workflow request.

The named `GitHub CLI Setup` conversation was retrieved through personal-context search. The retrieval provided a targeted summary, not the full original transcript. It records a proposed `ChatGHBridge`: Windows PowerShell poller, private GitHub RPC mailbox, known gh.exe operations, schemas, expiry/replay protection, repo mappings, permission tiers and Scheduled Task. These are prior proposals, not tested software.

The related `OcodeAgents` material was retrieved as context and checked against live `techrote/ansible` README/POLICY and issue #1. Its executable orchestrator remains an open implementation issue as observed during planning. No claim is made to having audited an installed local runner. Ansible here is the user's repository name; it is not evidence that Red Hat Ansible must be a runtime dependency.

## Requirements and preferences

| ID | Class | Reconciled statement | Execution coverage |
|---|---|---|---|
| R1 | R | Preserve ordinary Chat as the intended surface; do not silently spend Work/Codex/API allowance. | IL-011, IL-020, IL-021, IL-022 |
| R2 | R | Bundle terminal evidence in a repo-backed cache and send compact references. | IL-005 through IL-011 |
| R3 | R | Permit two-way structured local requests, initially including screenshots. | IL-002 through IL-004, IL-012 through IL-015 |
| R4 | R | Name is Interlocuator, CLI/project shorthand `interloc`. | IL-001, IL-008, IL-024 |
| R5 | R | Leave implementable docs, issues, dependencies, prompts, verification and an auditable ledger. | This planning baseline; IL-028 maintains automation |
| R6 | R | Consider GitHub CLI Setup/Ansible crossovers without destroying existing separation. | IL-016 through IL-019 |
| P1 | P | Lightweight terminal interaction is preferred to laggy Chat UI; Codex desktop itself is not the problem. | IL-020, IL-023; normal-Chat TUI remains gated |
| P2 | P | Windows-first, simple operation, low-friction approval and clear session/worktree identity. | IL-006, IL-008, IL-012, IL-023, IL-024 |
| D1 | D | Python broker + gh transport + optional Windows capture helper; selected here, not user-mandated. | IL-001, IL-005, IL-012 |
| D2 | D | Human-mediated message/attachment handoff is the MVP fallback. | IL-014, IL-021 |
| O1 | O | Automatic browser notifier, reply-mirroring TUI, arbitrary task execution, clipboard access and full-desktop capture. | Only narrow gated subset scheduled; others deferred |

## Repairs to earlier advice

**X: Codex CLI solves the user's Chat problem.** It does not satisfy R1. Codex/Work share metering where offered [S1]. No API substitution is allowed without a new explicit product decision.

**X: A browser extension is automatically a supported/acceptable consumer Chat transport.** No such support was established. Output extraction restrictions and missing stable mode/API contracts require IL-020. Browser automation is not made acceptable merely by avoiding private HTTP endpoints [S3].

**X: Ordinary Chat can never wait for tools.** An active assistant turn can invoke available tools and receive their results. The missing ability is an indefinite local process subscription or automatic new ordinary-Chat turn when GitHub changes. The baseline therefore uses a human message; provider-native event tasks must not be silently substituted.

**X: All Chat surfaces offer writable GitHub tools.** This session exposes write tools and the README bootstrap succeeded; the public GitHub help describes read-only access and availability varying by surface [S2]. Record both, without treating one as universal. IL-011 probes the user's exact surface and preserves a local import fallback.

**X: A private repo screenshot can simply be fetched and inspected.** The exposed repository fetch is text-oriented and explicitly rejects binary downloads. The private-user-image action is for GitHub attachment URLs, not ordinary repo PNG files. No vision path has been proved. IL-014 is mandatory before promising remote image delivery.

**X: Moving request files through pending/claimed/complete gives exactly-once RPC.** It does not survive crashes or concurrent writers. Use immutable requests, a local durable journal, one broker/device, idempotency keys and an explicit indeterminate state.

**X: `source: chatgpt` authenticates a requester; an HWND is a stable identity.** Neither is accepted. Local enrollment defines authority; screenshot targets use expiring opaque IDs bound to process creation identity, with revalidation.

**X: Private Git means disposable cache or no privacy risk.** Git retains history; deletion is not erasure. Keep raw logs and images local by default, sanitize exports and bound/rotate mailbox epochs.

**Target correction.** Stage 9's isolated `techrote/zaaggenz` conflicts with every other target reference. All work is scoped to `techrote/interloc`; no changes to zaaggenz or the integration repositories are authorized by this plan.
