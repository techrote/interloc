# Architecture and protocol specification

Version: planning contract v1, 2026-09-12. Normative MUST/MUST NOT applies to implementation; limits below are selected defaults, not measured performance. Security rules in [SECURITY](SECURITY.md) override convenience. [VERIFY](VERIFY.md) defines evidence.

## Components and ownership

`src/interloc/protocol/` validates JSON. `policy/` evaluates local grants. `broker/` owns SQLite transitions and capability dispatch. `transport/github/` owns all gh/network access. `collectors/` captures enrolled sessions. `evidence/` normalizes, redacts, chunks and describes artifacts. `retention/` manages bounded storage. `capabilities/capture/`, `capabilities/github/` and `integrations/ansible/` implement adapters. `cli/` is the local consent/control surface; `notify/` prepares human handoff; optional `tui/` does not become an alternative Chat backend.

Local root: `%LOCALAPPDATA%\Interlocuator` with `config/`, `state/`, `spool/`, `artifacts/`, `reports/`. It MUST be outside project worktrees. Logs/config must not contain GitHub tokens. Broker owns network credentials; helpers/children do not inherit them. Software updates require an explicit local action, independent of mailbox polling.

## Runtime repository layout

The configured private mailbox has enrolled repository ID, epoch UUID and branch names. Requests live on `inbox`; one broker writes its own `outbox/<device-id>` branch. These branches must be initialized explicitly during setup. The source repository is never used implicitly as a mailbox.

```
inbox branch:
  requests/<device-id>/<YYYY-MM>/<request-uuid>.json
outbox/<device-id> branch:
  responses/<YYYY-MM>/<request-uuid>.json
  sessions/<session-uuid>/events/<event-uuid>.json
  sessions/<session-uuid>/chunks/<chunk-uuid>.txt
  indexes/<group-uuid>.json
  status/device.json
```

Paths are transport-generated, never arbitrary requester-supplied filesystem paths. Requests are immutable individual files. Poll changed trees/commits on the configured branch; do not depend on GitHub code-search indexing or merely checking today's directory. Catch up over missed months with bounded pagination. Ref rewinds, epoch mismatch or unexpected repository visibility cause a pause and local review.

Each broker serializes all mutations to its outbox. Stage sanitized text objects and a manifest in one tree/commit and advance the branch without force. On ref conflict, refetch, compare immutable IDs/digests, rebuild with bounded retries; never discard someone else's changes. Request producers using Contents API serialize their own writes and use unique paths. Do not execute partial bundles. Branch separation does not prevent a credential holder from modifying another branch; local validation remains mandatory.

## Request v1

JSON UTF-8 object, exact properties only. Required: `schema_version` (1), UUID `request_id`, UUID `mailbox_epoch`, enrolled `target_device`, descriptive `requester_label`, RFC3339 UTC `created_at`/`expires_at`, `capability`, bounded `arguments`. Optional `session_id` is a UUID. The transport records origin repository ID, branch and full commit SHA separately; do not put a self-referential enclosing commit SHA in the request.

Default expiry is five minutes; maximum accepted lifetime is 24 hours; screenshot requests expire after at most two minutes. Default tolerated future clock skew is 60 seconds, with local warning/denial beyond it. Parse timezone-aware timestamps, use monotonic clocks for running deadlines, and never revive expired work after restart.

Allowed v1 core capabilities: `system.ping`, `terminal.tail`, `window.list`, `capture.window`. GitHub and Ansible extensions are separately registered and locally disabled until installed/enrolled. Capability schemas use a discriminated union, `additionalProperties: false`, bounded sizes, enums and explicit units. Unknown versions/capabilities are terminal rejections, not best-effort shell actions.

`terminal.tail` accepts an enrolled `session_id`, optional opaque cursor and `max_lines` 1..500. `window.list` accepts an enrolled `capture_scope_id`; only locally approved app/window inventory is returned. `capture.window` accepts an opaque `window_id`, `capture_scope_id` and `format: png`; no title regex or desktop fallback. Expiring window IDs are resolved and revalidated locally before capture. Ping contains only a bounded nonce and returns no host secrets.

## Responses and artifacts

Every response contains version, request ID, input SHA-256, outcome, completed timestamp, bounded error code/message and artifact descriptors. Outcomes: `succeeded`, `rejected`, `expired`, `cancelled`, `failed`, `indeterminate`. Capture success is distinct from delivery state (`local_only`, `manual_attachment_pending`, `remote_image_verified`). Do not assert vision delivery merely because capture succeeded.

Artifact descriptors contain UUID, media type, byte length, SHA-256, redaction status, delivery state and either a safe mailbox-relative path or an opaque local artifact ID. Local absolute paths do not go into remote manifests. Each descriptor binds the exact bytes after sanitization. Chunk descriptors additionally include sequence, ingest timestamps, logical line range and explicit truncation/gap markers. Never claim original cross-stream chronology when only arrival order was observed.

A notification contains mailbox owner/name, full outbox commit SHA, manifest path and group/request IDs. The commit SHA is filled after publication; it is not embedded self-referentially in that commit. Consumers fetch manifests and text at that exact ref and validate lengths/hashes. An index is discovery, not authorization. Local-only attachments require a separate explicit upload; no magical `@bridge` chat command is assumed to exist.

## Durable state and delivery semantics

Journal key: `(repository_id, mailbox_epoch, request_id)`. Store original input digest, policy revision, decision, attempt ID, timestamps, artifact IDs and publication/notifier state in a transaction. Same ID/same digest reuses recorded outcome. Same ID/different digest is quarantined. Persist dedupe tombstones across retention and restarts.

Internal states: received -> validated -> awaiting_approval or ready -> running -> result_saved -> published -> notification_pending -> acknowledged. Rejection/expiry/cancellation/failure are explicit outcomes. Publication and notification can retry without re-running an action. Approval means local consent, not remote request status. A local named mutex prevents two broker processes owning one device/state directory; no distributed lease claim is made.

A crash before execution may resume safely after expiry/policy checks. A crash after a possible side effect but before durable result becomes `indeterminate`; mutating operations MUST NOT automatically retry. Read-only/capture retries are permitted only when policy explicitly allows a fresh observation, timestamp and approval validity remain applicable. Capture of a changed target requires a new approval. Atomic temp-file rename precedes recording artifacts; reconcile orphan files on restart.

## Enrollment, limits and backpressure

An explicit local setup selects repository ID, private visibility, branch names, device/epoch, repo/worktree aliases, session sources, allowed capture targets and policy. Never automatically discover and enroll every repository/window. Use gh's existing authorized login without extracting/storing its token. Setup refuses an unexpected host or identity and does not print secrets.

Defaults: request <=32 KiB; text chunk <=32 KiB; manifest <=64 KiB and <=128 entries; each publication group <=256 KiB of text; queue <=100 pending requests/device; concurrent capabilities <=2 and captures <=1. Capture decoded dimensions <=4096 x 4096 and encoded PNG <=16 MiB; reject or explicitly request a smaller capture rather than silently change evidence. Parser nesting, decompression and allocation are bounded. UTF-8 split boundaries and partial final lines are handled without losing byte attribution.

Active polling: 30 seconds; idle: 120 seconds; bounded jitter and exponential backoff on transient errors. Respect server rate headers/Retry-After and authentication failures. Batch unsolicited text at most once per minute, serialize mutations with at least one second between them, and default to <=60 publication commits/hour/device. User-triggered events may flush sooner within budget. These settings are configurable locally within tested bounds, not remote command knobs.

Local raw spool defaults to 256 MiB/device and seven days, with disk-low backpressure; pending approved artifacts are protected from eviction until acknowledged or explicitly expired. Mailbox text retention defaults to seven days of active data with a 100 MiB size review threshold. Git history is not erased by deleting paths. Rotation/archive/purge requires a reviewed procedure and no automatic force-push. No screenshots in Git by default.

## Session collection

MVP supports a user-launched supervised command wrapper and explicitly mapped UTF-8/decodable log files. It does not attach to arbitrary existing terminal buffers. Optional pseudoconsole support is separate evidence-backed work; no OCR requirement.

Record session UUID, parent tool kind, repository/worktree alias, branch/HEAD before and after when available, dirty status, local command start/end, stream label, ingest sequence, exit code or `unknown`, timeout/cancel/rotation/gap events. Re-check cwd/HEAD after each command; do not attribute a child process or directory change to a stale shell snapshot. Never publish command environment wholesale; redact arguments and personal paths. Do not infer completion from silence alone. Agent structured logs may be consumed as inert data through an explicitly versioned adapter.

## Local UI contract

Planned commands: `interloc doctor`, `interloc run`, `interloc status`, `interloc pause`, `interloc request import`, `interloc approve`, `interloc collect`, `interloc handoff`. IL-001/IL-008 finalize parsers without changing semantics. `pause` blocks new execution/publication and clears queued automatic notification; status explains what is still finishing. Approval shows capability, target, data destination, request digest, expiry and effect. Local import validates the same schema/policy as remote requests. Handoff copies a pointer only on explicit user action and never reads clipboard contents.

CLI help and installation commands must say whether multiline blocks are pasted together or executed individually. Errors include a stable code, concrete cause, remedy and evidence path, without credentials. A missing prerequisite is visible, not an invitation to auto-install/elevate.
