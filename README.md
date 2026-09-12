# Interlocuator (`interloc`)

A Windows-first local capability broker and repo-backed courier for ordinary ChatGPT conversations and local development tools.

Interloc collects explicitly enrolled terminal output, publishes bounded evidence to a private GitHub mailbox, and handles policy-approved structured requests such as window screenshots. It is not Codex, a replacement ChatGPT service, or an unrestricted remote shell.

## Start here

- [Execution atlas and milestones](docs/ROADMAP.md): what to implement, dependencies, parallel lanes, release gates.
- [Agent context bundle](docs/AGENT_CONTEXT.md): reading routes and implementation instructions.
- [Protocol and architecture](docs/SPEC.md), [security](docs/SECURITY.md), [verification](docs/VERIFY.md).
- [Research and compatibility limits](docs/RESEARCH.md), [decisions](docs/DECISIONS.md), [conversation reconciliation](docs/PROVENANCE.md).
- [GitHub CLI / Ansible integrations](docs/INTEGRATIONS.md).
- [Execution ledger](docs/EXECUTION_LEDGER.md) and [machine-readable task graph](docs/workflow.json).

## Status

Repository-native planning baseline dated 2026-09-12. Implementation issues define the work; no runtime implementation, Windows capture success, unattended Chat operation, performance measurements, or quota experiment is claimed by these documents.

## Product boundary

The source repository is `techrote/interloc`. Deployment uses a separately configured private runtime mailbox and local state, not logs committed to the source tree. Ordinary Chat remains the intended reasoning surface. Interloc must not silently select Work/Codex or API billing.

The supported baseline is human-mediated: Interloc prepares a small evidence pointer or screenshot attachment; the user submits it in the intended conversation. Structured request handling and repo-backed responses can still remove most terminal copy/paste. Automatic Chat notification and a normal-Chat terminal frontend are gated research, not prerequisites for the core.

A text-file fetch does not prove that a private PNG reaches model vision. Screenshots remain local with explicit attachment handoff unless an authenticated image route is positively demonstrated. Remote messages are inert data; local policy controls execution.

`techrote/ansible` remains the human-initiated trusted execution plane. `techrote/intrallm` remains reference/task data. Interloc integrates with them without changing those trust boundaries.
