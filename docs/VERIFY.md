# Verification and release gates

This file specifies future tests. Nothing below is a claimed measurement. Every result records base/tested commit SHAs, OS/build, Python/gh/capture backend versions, configuration, command, exit code, elapsed time, expected/actual result and evidence path. Synthetic fixtures only in source control.

## Test layers

**Unit/contract:** JSON schema and runtime validators, timestamp/size/path limits, digest consistency, dedupe, policy, output sanitization, serialization and adapters. Property/fuzz tests include Unicode, mixed encodings, fragmented UTF-8, nested JSON, duplicate JSON keys, enormous declared lengths and schema evolution.

**Deterministic integration:** fake transport, fake monotonic clock, fake window provider, local SQLite and synthetic collector. Inject failures at each state transition: before execution, after effect, before result save, after commit but before notifier acknowledgement. Demonstrate no automatic retry of ambiguous writes and no rerun on publication retry.

**Windows integration:** real supervised subprocess streams, PowerShell 5.1 launcher compatibility and a current PowerShell where available, Python 3.12+ environments, Unicode/spaced paths, code pages, cancellation/child cleanup, multiple worktrees, locked files, reparse points, interactive-session restrictions and the capture acceptance selected by the current backend gate. Linux-only runs cannot satisfy this layer. For the interim Greenshot backend selected by IL-012, IL-013 requires one supervised installed-Greenshot capture of a harmless approved synthetic window plus its negative target/config cases; the earlier WGC/PrintWindow matrix remains deferred research rather than an IL-013 completion prerequisite.

**External acceptance:** a disposable private mailbox with actual gh auth and the chosen ordinary-Chat surface. Record human handoff steps separately from automatic ones. Use known text and known-shape screenshots; no personal desktop/password data.

## Required named test families

| Family | Required cases | Primary owner |
|---|---|---|
| T-contract | unknown version/capability, extra/duplicate keys, expiry/future clock, oversize, traversal/UNC/ADS, byte/digest mismatch | IL-002 |
| T-policy | forged source labels, unapproved scope, stale approval, destination change, deny-by-default | IL-003 |
| T-state | duplicate same/different digest, restart, journal recovery, single broker, crash ambiguity, cancel | IL-004 |
| T-transport | 304/403/404/409/429/5xx, retry-after, auth loss, ref conflict/rewind, pagination, missed-month backlog, visibility change, orphan commit | IL-005 |
| T-collect | stdout/stderr flooding, partial UTF-8, no newline, log rotation, branch/cwd drift, timeout, unknown exit, process death | IL-006 |
| T-evidence | seeded token/path leakage, OSC/ANSI injection, chunk bounds, gaps, hashes, local-only image metadata | IL-007 |
| T-approval | request digest display, expiry during approval, pause, deny, stale work, copied-pointer UX | IL-008, IL-021 |
| T-retention | disk full, quotas, pending artifact protection, tombstones, epoch rotation plan, export incident | IL-009 |
| T-capture | exact target/identity and foreground checks, handle/process reuse, unavailable/locked target, backend/config failure, timeout/ambiguity, blank/wrong-frame review and no desktop fallback. Interim Greenshot acceptance additionally requires one real installed-Greenshot synthetic-window smoke; WGC/PrintWindow GPU/DPI/minimization matrix is deferred unless native capture is resumed. | IL-012, IL-013 |
| T-vision | actual image input vs text-only link, private access failure, manual attachment, delivery status honesty | IL-014, IL-015 |
| T-gh | fixed repo/host/argv, no secret env, malicious config, current-state preview, ambiguous write recovery | IL-016, IL-017 |
| T-ansible | absent runner/status, schema mismatch, pinned SHA mismatch, stale generations, no remote executable update | IL-018, IL-019 |
| T-browser | terms/support decision, unknown mode, wrong tab, draft protection, queued message, resume/reload, no auto fallback | IL-020, IL-022 |
| T-release | clean install, explicit update/rollback, no-admin start/stop, restart recovery, offline operation, uninstall preserving data | IL-024, IL-027 |

## Proposed performance budgets

Use a recorded, modest Windows test machine; report hardware rather than inventing representativeness. A 30-minute synthetic soak with 8 enrolled streams, bursty 1 MiB/s aggregate input and a deliberately slow/failing transport must stay within configured spool/queue bounds, keep broker memory below a provisional 256 MiB target, and mark every dropped/truncated segment. It must never grow unbounded or block emergency pause behind output processing. Tune thresholds through an explicit decision if the target is not justified by measurements.

Local status/pause/approval acknowledgement target: p95 under 250 ms while loaded, excluding human response and OS scheduler stalls; hard safety fallback stops admission immediately if the worker queue is saturated. Native capture target: bounded completion or typed timeout within 10 seconds; no claim that every supported app yields a frame. Measure end-to-end publication separately: polling interval + batching window + measured network delay. Do not promise sub-second Git-backed chat interaction.

Networking: default <=120 active poll attempts/hour and <=60 publication commits/hour/device; pagination/retries are separately counted and bounded. Respect server instructions even if that makes targets unattainable. Size/queue defaults in SPEC are enforced with tiny-limit tests so test execution need not fill a real disk.

## Definition of done per issue

All acceptance criteria addressed; tests added and exact commands/results retained; expected artifacts exist; no undocumented interface changes; scope/locks respected; evidence report in `docs/evidence/IL-xxx.md`; manifest and issue reconciled; unsupported native/external tests clearly marked. A research issue can complete with a negative result plus a documented fallback and dependent gate state. A later explicit operator requirement amendment may narrow a gate; the superseded verification remains NOT RUN/deferred rather than being relabelled PASS.

## Supervised MVP release (IL-027)

Must demonstrate: fresh no-admin installation; explicit private-mailbox enrollment; enrolled command output with correct worktree attribution; sanitized bounded publication and immutable-ref retrieval in ordinary Chat or explicitly qualified manual attachment fallback; strict request import; ping and terminal-tail response; scoped window capture with local confirmation; actual manual image attachment/vision test; duplicate/restart/crash cases; pause; retention; and no automatic Work/Codex/API transition.

G-chat text retrieval failure does not prevent local development but must be disclosed: do not market the release as successful repo-connected ordinary Chat until it passes. G-vision may pass by the explicitly documented manual attachment route. Automatic notifier/TUI mirroring is not a core release criterion. GitHub write and Ansible execution/proposal extensions have their own qualification and are disabled if not verified.

IL-025 performs resilience/performance tests independently of feature authors; IL-026 performs adversarial security tests. IL-027 reviews their evidence rather than treating issue closure as proof. If Windows/Chat access is unavailable, complete offline work and leave release qualification blocked with exact missing tests, not fabricated screenshots or timings.
