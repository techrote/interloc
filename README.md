# Interlocuator (`interloc`)

A Windows-first local capability broker and repo-backed courier for ordinary ChatGPT conversations and local development tools.

Interloc is intended to collect explicitly enrolled terminal output, publish bounded evidence to a private GitHub mailbox, and handle locally approved structured requests. It is not Codex, a replacement ChatGPT service, or an unrestricted remote shell.

## Start here

- [Execution atlas](docs/ROADMAP.md): milestone hierarchy, actual issue numbers, dependencies and safe parallel work.
- [Agent context bundle](docs/AGENT_CONTEXT.md), [protocol](docs/SPEC.md), [security](docs/SECURITY.md), and [verification](docs/VERIFY.md).
- [Research](docs/RESEARCH.md), [decisions](docs/DECISIONS.md), [conversation reconciliation](docs/PROVENANCE.md), and [integration boundaries](docs/INTEGRATIONS.md).
- [Execution ledger](docs/EXECUTION_LEDGER.md), [external blockers](docs/BLOCKERS.md), [task manifest](docs/workflow.json), and [planning audit](docs/evidence/planning-audit.md).

## Current state

Planning baseline dated 2026-09-12: 25 GitHub issues, including the overview and one blocker-only tracker. The manifest retains 28 planned task IDs; four do not have published issues. External publication safeguards prevent calling the entire issue-deployment stage complete. See BLOCKERS before executing any affected task.

The independent implementation workflow starts at [issue 2 / IL-001](https://github.com/techrote/interloc/issues/2). No Interloc runtime, Windows capture, unattended Chat operation or performance result is claimed. The repository includes a working read-only planning auditor with 20 offline tests.

From a repository checkout, run these commands separately:

```text
python tools/audit_workflow.py
python -m unittest discover -s tests/workflow -v
```

The auditor never contacts GitHub or changes issues. `--graph-only` limits it to manifest checks; `--issues <file.json>` additionally checks a supplied complete REST-style issue snapshot. Readiness is a scheduling hint, not proof that prerequisite evidence passed.

## Product boundary

`techrote/interloc` is the source repository. Deployment uses a separately configured private runtime mailbox and local state. Raw logs, screenshots, credentials and runtime journals do not belong in this source tree.

Ordinary Chat remains the intended reasoning surface. The baseline is human-mediated: Interloc prepares an evidence pointer or reviewed attachment; the user submits it to the intended conversation. No silent Work/Codex/API substitution. Unsupported automatic transports remain gated, not prerequisites for the text courier.

A text-file fetch does not prove that a private image reaches model vision. Capture, storage and delivery are separately verified. Local policy controls actions; remote messages are data, not executable instructions or policy updates.

The Ansible/intrallm trust split is preserved in the design. Corresponding blocked integration work is not authorized by the existence of these documents. No changes to those repositories or zaaggenz were made in this planning run.
