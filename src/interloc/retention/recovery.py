"""Local reviewed incident helpers and non-executing mailbox review plans."""
from __future__ import annotations

from dataclasses import asdict
import re
from typing import Iterable

from interloc.broker import BrokerLock, Journal, RequestKey
from interloc.protocol import canonical_sha256
from .core import RetentionError, identifier


UNSTARTED = {"received", "validated", "awaiting_approval", "ready"}
TERMINAL = {"rejected", "expired", "cancelled", "failed", "indeterminate", "quarantined", "acknowledged"}


def _keys(keys: Iterable[RequestKey]) -> list[RequestKey]:
    # Do not exhaust an unbounded generator supplied by an integration.
    result = []
    for key in keys:
        if len(result) == 100 or not isinstance(key, RequestKey) or type(key.repository_id) is not int or key.repository_id < 1:
            raise RetentionError("INVALID_INCIDENT_SCOPE")
        identifier(key.mailbox_epoch)
        identifier(key.request_id)
        if key in result:
            raise RetentionError("INVALID_INCIDENT_SCOPE")
        result.append(key)
    return result


def _plan(journal: Journal, keys: list[RequestKey]) -> dict:
    entries = []
    for key in keys:
        row = journal.get(key)
        entries.append({**asdict(key), "sha256": row["input_sha256"], "state": row["state"],
                        "cancel_allowed": row["state"] in UNSTARTED})
    return {"operation": "cancel_unstarted_requests", "entries": entries,
            "review_sha256": canonical_sha256(entries)}


def revocation_preview(journal: Journal, keys: Iterable[RequestKey]) -> dict:
    """Inspect only locally selected requests while the broker is stopped.

    The caller must stop all other local state writers (including control CLIs).
    The broker owner lock additionally rejects a running broker. No field in a
    mailbox request can invoke this offline operator API.
    """
    selected = _keys(keys)
    with BrokerLock(journal.path.parent):
        return _plan(journal, selected)


def revoke_pending(journal: Journal, keys: Iterable[RequestKey], *, reviewed_sha256: str) -> dict:
    """Revalidate review then cancel only unstarted entries, preserving tombstones.

    Progress is deliberately idempotent, not one invented all-or-nothing action:
    a disk error can leave some entries cancelled. Re-preview after interruption.
    Already-started effects are neither cancelled nor advertised as undone.
    """
    selected = _keys(keys)
    with BrokerLock(journal.path.parent):
        plan = _plan(journal, selected)
        if plan["review_sha256"] != reviewed_sha256:
            raise RetentionError("REVIEW_STALE")
        results = []
        for key, entry in zip(selected, plan["entries"]):
            if entry["state"] in UNSTARTED:
                outcome = journal.cancel(key)
            elif entry["state"] in TERMINAL:
                outcome = entry["state"]
            else:
                outcome = "reconcile_started_work"
            results.append({"request_id": key.request_id, "outcome": outcome})
        return {"results": results, "running_effects_undone": False}


def mailbox_review(*, repository_id: int, epoch: str, snapshot_sha: str,
                   active_bytes: int, oldest_age_seconds: int, new_epoch: str | None = None) -> dict:
    """An inert bounded review descriptor, NOT gh commands or enrollment mutation."""
    if (type(repository_id) is not int or repository_id < 1 or type(active_bytes) is not int
            or not 0 <= active_bytes <= 2**63 - 1 or type(oldest_age_seconds) is not int
            or not 0 <= oldest_age_seconds <= 2**63 - 1
            or not isinstance(snapshot_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", snapshot_sha)):
        raise RetentionError("INVALID_MAILBOX_REVIEW")
    identifier(epoch)
    if new_epoch is not None:
        identifier(new_epoch)
        if new_epoch == epoch:
            raise RetentionError("EPOCH_UNCHANGED")
    plan = {"repository_id": repository_id, "epoch": epoch, "snapshot_sha": snapshot_sha,
            "active_bytes": active_bytes, "oldest_age_seconds": oldest_age_seconds,
            "size_review_due": active_bytes >= 100 * 1024 * 1024,
            "age_review_due": oldest_age_seconds >= 7 * 24 * 3600,
            "historical_bytes": "unknown", "history_erased": False,
            "proposed_epoch": new_epoch, "requires_local_enrollment": new_epoch is not None,
            "destructive_actions": [], "requires_human_review": True}
    return {**plan, "review_sha256": canonical_sha256(plan)}
