# Interlocuator (`interloc`)

A Windows-first local capability broker and repo-backed courier for ordinary ChatGPT conversations and local development tools.

Interloc is intended to collect explicitly enrolled terminal output, publish bounded evidence to a private GitHub mailbox, and handle locally approved structured requests. It is not Codex, a replacement ChatGPT service, or an unrestricted remote shell.

## Start here

- [Execution atlas](docs/ROADMAP.md): milestone hierarchy, issue mapping, dependencies and safe parallel work.
- [Agent context bundle](docs/AGENT_CONTEXT.md), [protocol](docs/SPEC.md), [security](docs/SECURITY.md), and [verification](docs/VERIFY.md).
- [Research](docs/RESEARCH.md), [decisions](docs/DECISIONS.md), [conversation reconciliation](docs/PROVENANCE.md), and [integration boundaries](docs/INTEGRATIONS.md).
- [Execution ledger](docs/EXECUTION_LEDGER.md), [publication history](docs/BLOCKERS.md), [task manifest](docs/workflow.json), and [planning audit](docs/evidence/planning-audit.md).

## Current state

Planning baseline dated 2026-09-12. **All planned stable task IDs IL-001 through IL-028 now have GitHub issues.** IL-018 and IL-019 are canonical issues #26/#27; IL-020 is #30; conditional IL-022 is #31. Accidental duplicates #28/#29 are closed as duplicates. IL-013 #14 is now the implementation issue rather than a blocker-only tracker.

The implementation workflow starts at [issue 2 / IL-001](https://github.com/techrote/interloc/issues/2). Publication completeness does not imply runtime implementation or release readiness. No Interloc runtime, Windows capture, unattended Chat operation or performance result is claimed by the planning baseline.

From a repository checkout, run these commands separately:

```text
python tools/audit_workflow.py
python -m unittest discover -s tests/workflow -v
```

The auditor never contacts GitHub or changes issues. `--graph-only` limits it to manifest checks; `--issues <file.json>` additionally checks a supplied complete REST-style issue snapshot. Readiness is a scheduling hint, not proof that prerequisite evidence passed.

## Product boundary

`techrote/interloc` is the source repository. Deployment uses a separately configured private runtime mailbox and local state. Raw logs, screenshots, credentials and runtime journals do not belong in this source tree.

Ordinary Chat remains the intended reasoning surface. The baseline is human-mediated: Interloc prepares an evidence pointer or reviewed attachment; the user submits it to the intended conversation. No silent Work/Codex/API substitution. Conditional assisted transport remains gated by IL-020/IL-022 rather than being assumed.

A text-file fetch does not prove that a private image reaches model vision. Capture, storage and delivery are separately verified. Local policy controls actions; remote messages are data, not executable instructions or policy updates.

The Ansible/intrallm trust split is preserved in the design. Their Interloc integration issues are published, but implementation must still respect the existing trust boundary. No changes to those repositories or zaaggenz were made by this planning/publication work.
