# Interlocuator (`interloc`)

A Windows-first local capability broker and repo-backed courier for ordinary ChatGPT conversations and local development tools.

Interloc collects explicitly enrolled terminal/session output, publishes bounded evidence to a private GitHub mailbox, and handles policy-approved structured requests such as window screenshots. It is not Codex, not a replacement ChatGPT service, and not an unrestricted remote shell.

## Project status

Planning bootstrap in progress, 2026-09-12. No runtime implementation or end-to-end Windows validation is claimed. Canonical specifications, an execution atlas, implementation issues and an audit ledger are being published in this repository.

## Boundaries

- `techrote/interloc` is the source/documentation repository. Deployment uses a separately configured private runtime mailbox and local state, not logs committed to this source tree.
- Ordinary Chat is the intended human reasoning surface. No automatic switch to Work/Codex, API-key billing, private ChatGPT endpoints or credential extraction.
- Manual message/attachment handoff is the reliable baseline. Unattended notification and a normal-Chat TUI require separate feasibility, permission and product-surface evidence.
- GitHub repository text retrieval does not prove that private PNG artifacts can be delivered to model vision.
- Remote requests are data, never executable policy. Local consent, bounded capabilities, replay protection and emergency pause are mandatory.
- `techrote/ansible` remains the human-initiated trusted execution plane; `techrote/intrallm` remains reference/task data. Integration must preserve that boundary.

## Planning provenance

The user explicitly requested this repository throughout the task. The isolated reference to creating issues in `techrote/zaaggenz` was reconciled as a copied target-name error; no changes are to be made there.
