# GitHub CLI and Ansible/intrallm integration contracts

See [PROVENANCE](PROVENANCE.md) for the recovered `GitHub CLI Setup` conversation and [RESEARCH](RESEARCH.md) S6/S11/S12 for sources. Do not mutate the integration repositories while implementing Interloc without separate issue scope/authority.

## gh.exe broker adapter

Transport gh operations and user-requested GitHub capabilities are different layers. The transport only accesses the enrolled private mailbox. Capability adapters may access separately enrolled project repositories; this must not expand the mailbox grant automatically.

Use fixed executable/hostname/repository and typed argument arrays. Runtime JSON must never contain arbitrary `gh api` endpoints, shell commands, HTTP headers, credentials or executable paths. Preserve GitHub API error/status/rate metadata in sanitized form. Capture stdout/stderr boundedly and never print tokens. `gh auth status` diagnostics should summarize state without collecting credential files.

Initial read-only capabilities (IL-016): `github.issue.read`, `github.issues.list`, `github.pr.read`, `github.checks.read`, and `git.worktree.status`. Each schema fixes allowed repo aliases and bounded IDs/pagination/fields. Local Git inspection is scoped and must not trigger external diff/textconv/config helpers. Native plugin limitations can be bridged by these approved operations, not by exposing raw gh authority.

Optional writes (IL-017): `github.issue.comment` and `github.issue.update` limited to title/body/state on enrolled repositories. No deletion, branch protection changes, secret management, force push, repository creation or workflow edits. Display a preview/diff and expected current state; confirmation expires and becomes invalid if target state changed. Deduplicate comments with a request-ID marker and reconcile a crash before retrying. The response must identify actual affected object IDs and before/after evidence; never claim success from exit status alone if the API result is ambiguous.

## Existing trust planes

`techrote/ansible` is the user's trusted human-initiated execution plane. `techrote/intrallm` is agent-visible reference/task data. Ansible policy explicitly separates executable updates from refreshed remote assignments. The planning-era issue #1 specified slots 1-4, fixed runners including noop_v1, monotonic generations, isolated local state and status/result files. The IL-018 implementation now pins the inspected producer source at `ab1f9023545539f334ebc23635b54c8b03eac40d`; see [ANSIBLE_TELEMETRY](ANSIBLE_TELEMETRY.md). An inspected repository contract is still not proof of an enrolled live installation.

## Status adapter (IL-018)

Implemented in PR #45 with [compatibility notes](ANSIBLE_TELEMETRY.md), [evidence](evidence/IL-018.md) and [CI receipt](evidence/IL-018-CI.md). The producer's per-job files lack slot/generation, so those are explicitly operator-mapped or reported unavailable. Reported success is not independent evidence verification. Optional reference fetching and automatic courier wiring are not implemented; the normative optional design below remains future scope.

Read explicitly mapped status JSONL/result files from a locally installed Ansible system, or synthetic fixtures when unavailable. Record source path alias, schema version, slot/run/generation, file offset and truncation/rotation events. Do not parse arbitrary natural-language instructions into commands. The adapter gracefully reports unavailable/stale/schema-mismatch without attempting to bootstrap or update Ansible.

An optional reference descriptor may identify intrallm repository/path/full commit/blob SHA. Fetch as data, verify pinned bytes and surface provenance. Never execute a referenced launcher or import code from intrallm. Share evidence schemas where justified; do not make all Interloc development depend on the Ansible runner shipping first.

## Fixed task proposal handoff (IL-019)

Remote request `ansible.task.propose` is disabled until local enrollment. It may propose a known slot/runner/generation with bounded validated arguments; it cannot select scripts/executables, alter runners or authorize its own launch. The local UI shows the exact proposal and hands it to the human-triggered trusted launcher workflow. Initial fixture uses noop_v1 only.

No autonomous dispatch to OMP/Codex or model billing is part of this issue. Human launch remains required, and no agent receives the broker's GitHub credentials. If the Ansible runner is absent or its contract differs, report the integration blocked, retain fixtures and continue core Interloc. A future authorized local named-task executor needs a separate decision and security qualification.

## Shared identity and concurrency

Use explicit repo/worktree/run aliases and pinned SHAs. Interloc observation does not grant authority to mutate a target repo, move its HEAD or reuse another agent's worktree. Do not infer that identical credentials mean independent authenticated actors. One broker serializes remote writes; multiple status readers can be concurrent. Executable and policy upgrades are always separate from mailbox refresh.
