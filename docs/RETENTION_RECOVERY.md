# Local retention and privacy recovery (IL-009)

This module is a local storage component, not a running courier or a new remote
capability. It is independent of the IL-008 control branch. IL-010 must compose
its storage gates with broker, collection, sanitization and transport. There is
no `interloc retention` command yet; do not infer a deployed daemon from this API.

## Owned storage and limits

`RetentionStore(root, store_id, initialize=True)` explicitly creates a **new**
local directory. Its parent must already exist. The root must be absolute,
outside project worktrees and free of symlink/junction ancestors. Existing
nonempty or empty directories are not silently adopted. Reopening requires the
same local UUID, exact versioned owner marker and intact catalog. Keep the store
under the configured Interlocuator local root, not the source repository.

A single process owns the store through an OS file lock. Methods are serialized
within that process. Call `close()` or use the context manager. The broker and
other producers must use this owner rather than write into `objects/` directly.
The catalog records generated UUID filenames; there is no remote path argument.
Unknown files, hardlinks, symlinks, junctions, missing catalogs, changed bytes and
inconsistent references require review rather than broad filesystem cleanup.

| Budget | Default | Meaning |
|---|---:|---|
| Payload bytes | 256 MiB | All available, writing and evicting owned payloads |
| Individual payload | 16 MiB | Further capability-specific limits still apply |
| Live objects | 10,000 | Zero-byte objects count too |
| Protected pending objects | 100 | No automatic eviction to make room |
| Unprotected age | 7 days | Measured from intake with the injected UTC clock |
| Artifact records | 100,000 | Includes permanent expired/evicted/failed IDs |
| Catalog | 32 MiB | SQLite page-count ceiling, separate from payload quota |
| Free-space reserve | 64 MiB | Plus payload and a 64 KiB admission allowance |

The specification supplies the 256 MiB, seven-day and bounded-pending policy.
The object/catalog/free-space ceilings are conservative implementation defaults,
not measured workload sizing. Local configuration can select tested smaller
bounds. They never become requester-controlled arguments. Catalog transactions
use a rollback journal, not an accumulating WAL. Temporary database journal and
filesystem metadata consume additional space; the payload quota is not a claim
that the entire filesystem or broker journal fits inside 256 MiB.

At catalog or tombstone capacity, intake stops. The system does not forget old
IDs to regain space. Archive/rotation needs a reviewed epoch and replay-state
preservation plan. Reducing limits around existing data also needs local review;
never manually delete catalog rows to make an old reference appear new.

## Protection, expiry and restart behavior

`put` reserves the ID, size and hash before writing. Bytes are written to a known
staging file, flushed, renamed within the store, then marked available. Reopening
checks file size and SHA-256. Complete staged/final data can be reconciled; an
absent or incomplete write becomes an explicit failed record and gap.

Every artifact is protected by default. Age or capacity alone cannot evict
pending evidence. Acknowledgement using the exact digest removes protection;
an explicitly supplied local expiry deadline or `expire(id, expected_sha256)`
can expire a reviewed pending attachment. Neither is a remote approval shortcut.
An expired deadline is reported unavailable even before maintenance removes the
bytes. Descriptors are local metadata, not protocol manifests or permission to
publish. `kind="text"` does **not** claim the bytes were sanitized.

Eviction records durable intent before unlinking. A crash before or after unlink
leaves an unavailable `evicting` record, which restart resolves to its explicit
`expired`/`evicted` tombstone. Missing or changed bytes are never returned as
available. Reusing an old ID cannot recreate its bytes or extend its protection.
Broker request records are separate and are never purged by retention.

This is process-crash recovery with explicit checks, not a guarantee against
power loss, dishonest disk flushes or a compromised OS account. SQLite atomicity
does not make external files part of its transaction. The implementation follows
its own durable file-intent protocol and rechecks actual files after restart.
See the primary references below. Large-store hashing and resource/soak behavior
remain IL-025 qualification, not inferred from tiny synthetic tests.

## Composition contract for IL-010

Before collection or publication, inspect `status()` and honor
`collection_allowed` / `publication_allowed`. Any exception is also a stop, not
permission to continue using an earlier status. On backpressure, report the
stable reason, gap count and dropped-byte count. These are bounded aggregates,
not an unbounded copy of rejected logs. If disk failure prevents persisting the
failure itself, an in-memory gate still refuses admission; restart must reconcile
state before more work. Never fall back to an unmanaged raw spool.

Publication must call `read(id)` and build/validate the existing evidence-layer
manifest for the exact bytes. Do not publish raw or attachment payloads by
relabelling their kind. Hold evidence pending while approved publication/handoff
is outstanding. Missing, failed, expiry-due, expired and evicted descriptors must
be surfaced as unavailable, not offered as working attachment references.

A reviewed `resume(expected_revision)` reconciles ownership/state before clearing
a pause. It is not automatic and does not approve requests. Offline incident
cancellation requires the broker **and all other local journal writers** to be
stopped. `revocation_preview` creates a digest-bound preview for at most 100
locally selected request keys; `revoke_pending` rechecks it and calls the broker's
public cancellation API only for unstarted work. The broker owner lock rejects a
running broker. Already-started work is reported for reconciliation, not undone.
A disk error can leave a partially completed cancellation batch; re-preview and
reconcile rather than claiming all-or-nothing effects. Replay records survive.

## Incident procedure

Stop admission and publication; stop all local state writers before offline
recovery. Use the store's incident pause to suspend automatic age/quota cleanup.
Inventory only opaque artifact/request IDs, hashes, byte counts and states in
bounded pages. Keep sensitive content local. Inspect started requests and exact
published commit references separately; a local artifact inventory is not proof
of every copy that might already exist elsewhere.

Review and revoke pending consent through the bound cancellation preview. Do not
cancel a running effect and report that it never occurred. Preserve minimal
incident metadata and replay tombstones. When credentials may be exposed,
rotate/revoke them through the account provider's explicit operator procedure;
this module neither reads tokens nor performs external credential revocation.
Verify private mailbox identity, visibility, epoch and branch configuration
before resuming. An ownership mismatch is not fixed by deleting unknown files.

Distinguish these three operations before approving any cleanup:

| Operation | What it changes | What it does not establish |
|---|---|---|
| Reviewed local expiry | One owned payload plus its local tombstone | Deletion of backups, remote commits or user attachments |
| Normal current-tree cleanup | Files visible in a later mailbox commit | Erasure of prior commits or existing clones |
| Destructive history/remote purge | Separately reviewed remote history or objects | Recall of every copied/downloaded third-party artifact |

No branch deletion, repository deletion, force-push, destructive history rewrite
or public hosting fallback is implemented. These actions require separate human
authorization and a reviewed procedure. Do not advertise path deletion as secure
erasure of Git history or of local storage media.

## Mailbox and epoch review

`mailbox_review` accepts locally observed active byte/age measurements and an
exact source snapshot SHA. It flags 100 MiB active size and seven-day active age
for review, explicitly says historical size is unknown, and emits no executable
commands. A proposed new epoch is only a plan; local enrollment, initialization
of new branches, frozen old-epoch admission, approval expiry and preserved replay
state must be reviewed separately. Old-epoch requests must never silently become
new work. Confirm retention of references needed for incident reconciliation.

## Reproduction and primary references

Run these commands **separately** from an installed source checkout:

```powershell
python -m unittest discover -s tests/retention -v
python -m unittest discover -s tests -v
python tools/audit_workflow.py
```

Tests use synthetic temporary directories, deterministic clocks, tiny limits,
injected process-crash points, simulated ENOSPC, actual SQLite page-limit failure,
unknown/link ownership cases and the broker's existing public APIs. They do not
consume real logs, capture a desktop, or mutate a live runtime mailbox.

Implementation references, consulted 2026-09-13:

- [SQLite atomic commit and failure assumptions](https://www.sqlite.org/atomiccommit.html)
- [SQLite maximum page count and synchronous settings](https://www.sqlite.org/pragma.html#pragma_max_page_count)
- [Python 3.13 os: fsync and replace](https://docs.python.org/3.13/library/os.html)
