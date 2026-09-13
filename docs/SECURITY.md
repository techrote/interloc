# Threat model, privacy and local authority

Normative companion to [SPEC](SPEC.md). Security is part of every implementation issue; IL-026 independently verifies it.

## Assets and adversaries

Protect GitHub credentials, local files and screenshots, project/worktree integrity, trusted runner code, correct conversation targeting, and the user's usage/account boundary. Threats include prompt injection in terminal output/README/screenshots, compromised mailbox writers, stale/replayed requests, malformed JSON/paths/images, another browser origin reaching local services, malicious Git configuration, accidental wrong-window capture and crash-induced repeated writes.

No design claim is made to protect against a fully compromised OS account or an administrator. The broker should still avoid multiplying their access through easy credential export.

## Authority

Remote actor labels, Git author names and paths are not authenticated identities. The broker trusts only explicit local enrollment/policy, applies least privilege to ALL mailbox requests, and can require local confirmation regardless of source. Repository/branch names are coordination boundaries, not cryptographic isolation. A hash proves byte consistency, not the author's identity.

Policy files live locally, schema-validated and permission-restricted. Requests cannot change grants, allowlists, paths, timeouts, code, runner registrations or auto-update settings. All grants bind mailbox ID/epoch, target device, capability, enrolled scope, expiry and destination. Deny-by-default. No wildcard auto-approval for `capture.*`, clipboard or arbitrary files.

## Consent classes

Ping and tail of an explicitly enrolled, sanitized session may be locally pre-approved. Window inventory is scoped and can itself disclose private titles, so it requires enrollment and redaction. Window capture requires preview/confirmation by default; optional session-limited consent binds the exact app/process identity and allowed region. Never choose the active window merely because a requester omitted a target.

Full desktop/monitor, clipboard reading, raw file access, **general** key/mouse injection, arbitrary shell and arbitrary gh arguments are outside MVP. The interim Greenshot capture provider has one narrow internal exception: after exact target, foreground and local-consent checks, it may emit only Greenshot's locally configured PrintScreen-based window-capture chord. Request data cannot name keys, modifiers or chords, the provider never focuses a target window, and no general input capability is exposed. `clipboard.write` is not a remote capability: local handoff may write a prepared pointer only on a deliberate user action. A later capability needs its own threat assessment and tests.

Mutating GitHub actions and trusted-task proposals require a visible local preview, destination, scope and confirmation bound to canonical request digest, policy revision, expiry and relevant current state. Revalidate after approval and immediately before use. Changed issue/repo state invalidates stale approval rather than applying blindly.

## Execution and path hardening

Use fixed executable identities and typed argument arrays with shell=False; no eval, Invoke-Expression, string-interpolated shells or remote-supplied subprocess environment. Allowlist environment variables; do not pass GH_TOKEN, GITHUB_TOKEN, SSH_AUTH_SOCK or host Git credential/config state into helpers/agent workers. gh uses a broker-only auth context and a fixed github.com hostname. Errors must not dump auth/config.

Map repository/session/scope IDs locally. Reject traversal, absolute remote paths, UNC paths, device namespaces, alternate data streams and escaping symlinks/reparse points. Resolve final handles where needed and test race conditions. Limit output, runtime, child-process lifetime and memory. Local Git inspection must disable external diff/textconv/helpers where applicable and must not execute hooks or mutate repository state. A dirty worktree is reported, never reset or stashed automatically.

Mailboxes supply only data. Never run a script, import Python, source PowerShell, load a plugin or update an executable from fetched mailbox content. A trusted installed broker may be updated only by a separate human-approved software update procedure with version/integrity checks. Do not introduce `pull_request_target` workflows executing untrusted changes with write secrets.

## Capture safeguards

Opaque window IDs expire and bind device, interactive desktop/session, process ID plus creation identity, window handle and scope. HWND reuse/title matches alone do not authorize capture. Revalidate immediately before capture; reject closed/replaced/elevated/inaccessible/protected/locked-session targets. Scoped failure never falls back to the whole desktop. Black/blank frames are not automatically a valid screenshot; record a diagnostic and support retry with human review.

For the interim Greenshot adapter, the approved target must already be the foreground window; Interloc does not activate or focus it. Greenshot must be locally configured for non-interactive window capture, `FileDefault`-only PNG output, clipboard-path copying disabled, and the enrolled local capture directory. Configuration mismatch, no output, multiple changed outputs, target-identity drift or foreground drift fails closed. Interloc never changes Greenshot configuration automatically and never substitutes region, monitor or desktop capture.

Preview can itself contain secrets; keep it local. Image redaction is not a guarantee, and OCR is neither required nor a reliable secret detector. Explicit consent, target restriction and manual image review are primary controls. Preserve artifact hashes and distinguish original from any user-approved edited export.

## Data and publication

Sanitize before commit/upload/notification, not after. Strip ANSI/OSC controls including hyperlinks/clipboard escapes; retain safe text with explicit truncation. Mask configured secrets and tokens, redact personal absolute paths and optionally window titles. A seeded-secret test is required, but no regex scanner may claim complete prevention. Default raw evidence stays local with bounded retention.

Private GitHub data still leaves the machine and enters Git history and a third-party service. No public repository, public object storage, token-in-URL or credential replay fallback. Authenticated remote image delivery is a separate capability gate; base64 text is not model vision.

Source files contain synthetic fixtures only. A public-visibility change, unexpected remote, invalid epoch, credentials failure, ref rewind or policy failure pauses publication. Rate limits/backpressure must never cause unsanitized bypass.

## Broker/UI exposure

Baseline has no listening TCP service. Prefer stdin/stdout or restricted local IPC. Optional browser work must use explicitly registered/pinned native messaging origins/extension IDs or a separately reviewed authenticated IPC design; no unauthenticated localhost command endpoint. An unknown tab/mode/conversation, an existing composer draft, a pending send or a stale response always stops automatic action. Existing drafts are never overwritten.

## Incident and recovery requirements

Provide emergency pause, local audit events, revocation of pending approvals, credential-rotation guidance, mailbox disablement and inventory of possibly exported artifacts. Explain that Git deletion does not remove historic copies. Destructive history rewrite, remote branch/repo deletion and external credential revocation require explicit human authorization. Preserve minimal non-secret incident metadata and replay tombstones; never silently delete evidence to hide a failure.
