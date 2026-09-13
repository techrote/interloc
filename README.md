# Interlocuator (`interloc`)

A Windows-first local capability broker and repo-backed courier for ordinary ChatGPT conversations and local development tools.

Interloc is intended to collect explicitly enrolled terminal output, publish bounded evidence to a private GitHub mailbox, and handle locally approved structured requests. It is not Codex, a replacement ChatGPT service, or an unrestricted remote shell.

## Start here

- [Execution atlas](docs/ROADMAP.md): milestone hierarchy, issue mapping, dependencies and safe parallel work.
- [Agent context bundle](docs/AGENT_CONTEXT.md), [protocol](docs/SPEC.md), [security](docs/SECURITY.md), and [verification](docs/VERIFY.md).
- [Research](docs/RESEARCH.md), [decisions](docs/DECISIONS.md), [conversation reconciliation](docs/PROVENANCE.md), and [integration boundaries](docs/INTEGRATIONS.md).
- [Latest continuation](docs/CONTINUATION_IL016.md), [execution ledger](docs/EXECUTION_LEDGER.md), [publication history](docs/BLOCKERS.md), [task manifest](docs/workflow.json), and [planning audit](docs/evidence/planning-audit.md).

## Current state

Planning baseline dated 2026-09-12. **All planned stable task IDs IL-001 through IL-028 now have GitHub issues.** IL-018 and IL-019 are canonical issues #26/#27; IL-020 is #30; conditional IL-022 is #31. Accidental duplicates #28/#29 are closed as duplicates. IL-013 #14 is now the implementation issue rather than a blocker-only tracker.

Runtime component implementation is underway. This tree includes foundation, protocol/policy, broker, private-mailbox transport, enrolled collection, sanitization, retention, read-only Ansible telemetry and the opt-in [GitHub/worktree read adapters](docs/READ_ADAPTERS.md). IL-016 adds 68 tests to the 183-test prerequisite baseline: **251 discovered tests**, with platform-specific skips. Exact tested SHAs, successful Windows/Ubuntu Python 3.12/3.13/3.14 code CI and native limitations are retained in [IL-016 evidence](docs/evidence/IL-016.md).

The approval/control CLI is implemented and tested on a separate **unmerged** branch; the current CLI still exposes help/doctor only. Its PR-creation block and outstanding interactive Windows Terminal check are recorded rather than worked around. The full text courier, live mailbox, ordinary-Chat and visual/release gates are **not qualified**.

Start continuation at the [IL-016 ledger supplement](docs/CONTINUATION_IL016.md), then the [previous handover](docs/SESSION_HANDOVER_2026-09-13.md); do not repeat foundation work. IL-016 supplies an independent read component, not the blocked approval/control path. IL-010 text integration, IL-017 confirmed issue writes and IL-019 task proposals still require merged IL-008. Native capture feasibility IL-012 requires its own interactive Windows evidence. Confirm actual PR/issue state and passing merged evidence before assigning dependent work.

From an installed development checkout, run these commands separately:

```text
python -m unittest discover -s tests -v
python tools/audit_workflow.py
python -m unittest discover -s tests/workflow -v
```

Installation is described in [DEVELOPMENT](docs/DEVELOPMENT.md). The auditor never contacts GitHub or changes issues. `--graph-only` limits it to manifest checks; `--issues <file.json>` additionally checks a supplied complete REST-style issue snapshot. Its default empty-completion readiness output is not the current execution frontier. Readiness is a scheduling hint, not proof that prerequisite evidence passed.

## Product boundary

`techrote/interloc` is the source repository, observed public during the IL-016 continuation. Deployment uses a separately configured private runtime mailbox and local state. Raw logs, screenshots, credentials and runtime journals do not belong in this source tree. No visibility change was made by the IL-016 implementation.

Ordinary Chat remains the intended reasoning surface. The baseline is human-mediated: Interloc prepares an evidence pointer or reviewed attachment; the user submits it to the intended conversation. No silent Work/Codex/API substitution. Conditional assisted transport remains gated by IL-020/IL-022 rather than being assumed.

A text-file fetch does not prove that a private image reaches model vision. Capture, storage and delivery are separately verified. Local policy controls actions; remote messages are data, not executable instructions or policy updates.

The Ansible/intrallm trust split is preserved. [Read-only telemetry](docs/ANSIBLE_TELEMETRY.md) has a pinned producer-format profile and synthetic verification; it does not execute the external runner or certify its reported success. [Retention and privacy recovery](docs/RETENTION_RECOVERY.md) are implemented as a local component. These components and the disabled-by-default read adapters are not automatically wired into a courier service. No changes to the external repositories are part of this work.
