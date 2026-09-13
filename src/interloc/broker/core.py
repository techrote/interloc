"""Durable request journal and crash-aware dispatcher.

The broker owns durable state transitions only. Capability implementations are
registered locally; remote requests cannot select Python callables or commands.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4

from interloc.policy import Decision
from interloc.protocol import canonical_json, canonical_sha256, validate_request

SCHEMA_VERSION = 2
PENDING_STATES = {"received", "validated", "awaiting_approval", "ready", "running", "result_saved", "published", "notification_pending"}
TERMINAL_STATES = {"acknowledged", "rejected", "expired", "cancelled", "failed", "indeterminate", "quarantined"}
ALL_STATES = PENDING_STATES | TERMINAL_STATES


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def utc_text(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RequestKey:
    repository_id: int
    mailbox_epoch: str
    request_id: str


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: str
    handler: Callable[[Mapping[str, Any], "ExecutionContext"], Mapping[str, Any]]
    mutating: bool = False
    retry_after_crash: bool = False

    def validate(self) -> None:
        if not self.name or "." not in self.name:
            raise BrokerError("REGISTRY_INVALID", "capability name must be explicit and namespaced")
        if not callable(self.handler):
            raise BrokerError("REGISTRY_INVALID", "capability handler must be callable")
        if self.mutating and self.retry_after_crash:
            raise BrokerError("REGISTRY_INVALID", "mutating capability cannot auto-retry after crash")


class CapabilityRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        spec.validate()
        if spec.name in self._specs:
            raise BrokerError("REGISTRY_DUPLICATE", f"capability {spec.name!r} already registered")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise BrokerError("CAPABILITY_UNAVAILABLE", f"capability {name!r} is not registered locally") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))


class BrokerLock(AbstractContextManager["BrokerLock"]):
    """Cross-platform single-owner lock for one state directory."""
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.path = state_root / "broker.lock"
        self._handle = None

    def acquire(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise BrokerError("BROKER_ALREADY_RUNNING", "state directory is already owned") from exc
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise BrokerError("BROKER_ALREADY_RUNNING", "state directory is already owned") from exc
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "BrokerLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    key: RequestKey
    attempt_id: str
    cancellation_requested: Callable[[], bool]


class Journal:
    def __init__(self, path: Path, *, queue_limit: int = 100) -> None:
        if queue_limit < 1 or queue_limit > 10_000:
            raise BrokerError("CONFIG_INVALID", "queue_limit must be 1..10000")
        self.path = path
        self.queue_limit = queue_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._db = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def _migrate(self) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise BrokerError("STATE_NEWER_VERSION", "database schema is newer than this software")
        with self.transaction():
            # Re-read under the writer transaction: two CLI processes may open together.
            version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                self._db.execute("""CREATE TABLE requests (
                    repository_id INTEGER NOT NULL, mailbox_epoch TEXT NOT NULL,
                    request_id TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                    request_json BLOB NOT NULL, capability TEXT NOT NULL,
                    state TEXT NOT NULL, policy_revision INTEGER NOT NULL,
                    policy_decision TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    attempt_id TEXT, attempt_started_at TEXT,
                    possible_effect INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    result_json BLOB, artifact_ids_json BLOB, publication_ref TEXT,
                    notification_ref TEXT, terminal_code TEXT,
                    PRIMARY KEY (repository_id, mailbox_epoch, request_id))""")
                self._db.execute("""CREATE TABLE collisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, repository_id INTEGER NOT NULL,
                    mailbox_epoch TEXT NOT NULL, request_id TEXT NOT NULL,
                    existing_sha256 TEXT NOT NULL, conflicting_sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL)""")
                self._db.execute("CREATE INDEX requests_state_idx ON requests(state)")
                version = 1
            if version == 1:
                self._db.execute("""CREATE TABLE local_authority (
                    repository_id INTEGER NOT NULL, mailbox_epoch TEXT NOT NULL,
                    request_id TEXT NOT NULL, binding_json BLOB NOT NULL,
                    approval_json BLOB,
                    PRIMARY KEY (repository_id, mailbox_epoch, request_id),
                    FOREIGN KEY (repository_id, mailbox_epoch, request_id)
                    REFERENCES requests(repository_id, mailbox_epoch, request_id))""")
                self._db.execute("CREATE TABLE control_state (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL)")
                self._db.execute("INSERT INTO control_state VALUES(1,0)")
                self._db.execute("PRAGMA user_version=2")
            elif version != SCHEMA_VERSION:
                raise BrokerError("STATE_MIGRATION_REQUIRED", "unsupported schema version")

    @contextmanager
    def transaction(self):
        """Serialize short state transitions, including across SQLite connections.

        Handlers and human prompts MUST NOT run inside this transaction.
        """
        with self._mutex:
            outer = not self._db.in_transaction
            if outer:
                self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                if outer:
                    self._db.commit()
            except BaseException:
                if outer:
                    self._db.rollback()
                raise

    def _pending_count(self) -> int:
        placeholders = ",".join("?" for _ in PENDING_STATES)
        return int(self._db.execute(f"SELECT COUNT(*) FROM requests WHERE state IN ({placeholders})", tuple(PENDING_STATES)).fetchone()[0])

    def receive(self, repository_id: int, request: Mapping[str, Any], decision: Decision, *, now: datetime | None = None, binding: Mapping[str, Any] | None = None) -> tuple[RequestKey, str]:
        normalized = validate_request(dict(request), now=now)
        digest = canonical_sha256(normalized)
        if type(repository_id) is not int or repository_id <= 0:
            raise BrokerError("ORIGIN_INVALID", "repository identity must be a positive integer")
        if digest != decision.request_sha256:
            raise BrokerError("DECISION_DIGEST_MISMATCH", "policy decision is not bound to this request")
        key = RequestKey(repository_id, normalized["mailbox_epoch"], normalized["request_id"])
        encoded = canonical_json(normalized)
        timestamp = utc_text(now)
        collision = False
        with self.transaction():
            row = self._db.execute(
                "SELECT input_sha256,state FROM requests WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (key.repository_id, key.mailbox_epoch, key.request_id),
            ).fetchone()
            if row:
                if row["input_sha256"] == digest:
                    return key, str(row["state"])
                self._db.execute(
                    "INSERT INTO collisions(repository_id,mailbox_epoch,request_id,existing_sha256,conflicting_sha256,observed_at) VALUES(?,?,?,?,?,?)",
                    (key.repository_id, key.mailbox_epoch, key.request_id, row["input_sha256"], digest, timestamp),
                )
                self._db.execute(
                    "UPDATE requests SET state='quarantined',terminal_code='REQUEST_ID_COLLISION',updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                    (timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
                )
                collision = True
            else:
                if self.is_paused():
                    raise BrokerError("PAUSED", "new request admission is paused locally")
                if self._pending_count() >= self.queue_limit:
                    raise BrokerError("QUEUE_FULL", "pending request limit reached")
                if decision.action == "deny":
                    state, terminal = "rejected", decision.code
                elif decision.action == "confirm":
                    state, terminal = "awaiting_approval", None
                elif decision.action == "allow":
                    state, terminal = "ready", None
                else:
                    raise BrokerError("DECISION_INVALID", "unknown policy action")
                self._db.execute(
                    """INSERT INTO requests(repository_id,mailbox_epoch,request_id,input_sha256,request_json,capability,state,policy_revision,policy_decision,created_at,updated_at,expires_at,terminal_code)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key.repository_id, key.mailbox_epoch, key.request_id, digest, encoded, normalized["capability"], state, decision.policy_revision, decision.action, timestamp, timestamp, normalized["expires_at"], terminal),
                )
                if binding is not None:
                    self._db.execute("INSERT INTO local_authority VALUES(?,?,?,?,NULL)",
                                     (key.repository_id, key.mailbox_epoch, key.request_id, canonical_json(dict(binding))))
        if collision:
            raise BrokerError("REQUEST_ID_COLLISION", "same request ID was observed with different canonical bytes")
        return key, state

    def is_paused(self) -> bool:
        with self._mutex:
            return bool(self._db.execute("SELECT paused FROM control_state WHERE id=1").fetchone()[0])

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise BrokerError("CONFIG_INVALID", "pause state must be boolean")
        with self.transaction():
            self._db.execute("UPDATE control_state SET paused=? WHERE id=1", (int(paused),))

    def status(self, *, limit: int = 100) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BrokerError("LIMIT_INVALID", "status limit must be 1..100")
        with self._mutex:
            counts = {r[0]: r[1] for r in self._db.execute("SELECT state,COUNT(*) FROM requests GROUP BY state")}
            rows = self._db.execute("""SELECT repository_id,mailbox_epoch,request_id,capability,
                input_sha256,state,expires_at,terminal_code FROM requests
                ORDER BY updated_at DESC,repository_id,mailbox_epoch,request_id LIMIT ?""", (limit,)).fetchall()
            return {"paused": self.is_paused(), "counts": counts, "requests": [dict(r) for r in rows],
                    "truncated": sum(counts.values()) > limit, "running": counts.get("running", 0)}

    def authority(self, key: RequestKey) -> dict[str, Any]:
        with self._mutex:
            row = self._db.execute("SELECT binding_json,approval_json FROM local_authority WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                                   (key.repository_id, key.mailbox_epoch, key.request_id)).fetchone()
            if row is None:
                raise BrokerError("AUTHORITY_MISSING", "request has no durable local authority binding; re-import with a new ID")
            return {"binding": json.loads(row[0]), "approval": json.loads(row[1]) if row[1] is not None else None}

    def store_approval(self, key: RequestKey, approval: Mapping[str, Any]) -> None:
        with self.transaction():
            self.authority(key)
            self._db.execute("UPDATE local_authority SET approval_json=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                             (canonical_json(dict(approval)), key.repository_id, key.mailbox_epoch, key.request_id))

    def deny(self, key: RequestKey, *, code: str = "LOCAL_DENIED", now: datetime | None = None) -> str:
        if code not in {"LOCAL_DENIED", "APPROVAL_REVOKED", "AUTHORITY_CHANGED"}:
            raise BrokerError("DECISION_INVALID", "unsupported local denial code")
        with self.transaction():
            row = self.get(key)
            if row["state"] in TERMINAL_STATES:
                return row["state"]
            if row["state"] in {"running", "result_saved", "published", "notification_pending"}:
                raise BrokerError("TOO_LATE", "work has started; pause and reconcile any completed effects")
            self._db.execute("UPDATE requests SET state='rejected',terminal_code=?,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                             (code, utc_text(now), key.repository_id, key.mailbox_epoch, key.request_id))
            self._db.execute("UPDATE local_authority SET approval_json=NULL WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                             (key.repository_id, key.mailbox_epoch, key.request_id))
            return "rejected"

    def get(self, key: RequestKey) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM requests WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
            (key.repository_id, key.mailbox_epoch, key.request_id),
        ).fetchone()
        if row is None:
            raise BrokerError("REQUEST_NOT_FOUND", "request is not in the journal")
        result = dict(row)
        result["request"] = json.loads(bytes(row["request_json"]).decode("utf-8"))
        if row["result_json"] is not None:
            result["result"] = json.loads(bytes(row["result_json"]).decode("utf-8"))
        return result

    def approve(self, key: RequestKey, decision: Decision, *, now: datetime | None = None) -> None:
        timestamp = utc_text(now)
        with self.transaction():
            row = self.get(key)
            if row["expires_at"] <= timestamp:
                raise BrokerError("EXPIRED", "request expired before approval")
            if self.is_paused():
                raise BrokerError("PAUSED", "approval is paused locally")
            if row["state"] != "awaiting_approval":
                raise BrokerError("STATE_CONFLICT", "request is not awaiting approval")
            if decision.action != "allow" or decision.request_sha256 != row["input_sha256"]:
                raise BrokerError("APPROVAL_INVALID", "approval decision is not an allow bound to the request")
            if decision.policy_revision != row["policy_revision"]:
                raise BrokerError("APPROVAL_INVALID", "policy revision changed since admission")
            self._db.execute(
                "UPDATE requests SET state='ready',policy_decision='allow',updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )

    def expire_due(self, *, now: datetime | None = None) -> int:
        timestamp = utc_text(now)
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE requests SET state='expired',terminal_code='EXPIRED',updated_at=? WHERE state IN ('received','validated','awaiting_approval','ready') AND expires_at<=?",
                (timestamp, timestamp),
            )
            return cursor.rowcount

    def cancel(self, key: RequestKey, *, now: datetime | None = None) -> str:
        timestamp = utc_text(now)
        with self.transaction():
            row = self.get(key)
            state = row["state"]
            if state in TERMINAL_STATES:
                return state
            if state == "running":
                self._db.execute(
                    "UPDATE requests SET cancel_requested=1,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                    (timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
                )
                return "running"
            self._db.execute(
                "UPDATE requests SET state='cancelled',terminal_code='CANCELLED',updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )
            return "cancelled"

    def cancellation_requested(self, key: RequestKey) -> bool:
        return bool(self.get(key)["cancel_requested"])

    def start(self, key: RequestKey, *, possible_effect: bool, now: datetime | None = None) -> str:
        timestamp = utc_text(now)
        attempt = str(uuid4())
        with self.transaction():
            row = self.get(key)
            if self.is_paused():
                raise BrokerError("PAUSED", "execution is paused locally")
            if row["expires_at"] <= timestamp:
                raise BrokerError("EXPIRED", "request expired before execution")
            if row["state"] != "ready":
                raise BrokerError("STATE_CONFLICT", f"cannot start request from state {row['state']!r}")
            self._db.execute(
                "UPDATE requests SET state='running',attempt_id=?,attempt_started_at=?,possible_effect=?,cancel_requested=0,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (attempt, timestamp, 1 if possible_effect else 0, timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )
        return attempt

    def save_result(self, key: RequestKey, result: Mapping[str, Any], *, state: str = "result_saved", terminal_code: str | None = None, now: datetime | None = None) -> None:
        if state not in {"result_saved", "failed", "cancelled", "indeterminate"}:
            raise BrokerError("STATE_INVALID", "invalid result state")
        timestamp = utc_text(now)
        encoded = canonical_json(dict(result))
        artifacts = result.get("artifact_ids", [])
        if not isinstance(artifacts, list) or not all(isinstance(x, str) for x in artifacts):
            raise BrokerError("RESULT_INVALID", "artifact_ids must be a list of strings")
        with self.transaction():
            row = self.get(key)
            if row["state"] != "running":
                raise BrokerError("STATE_CONFLICT", f"cannot save result from state {row['state']!r}")
            self._db.execute(
                "UPDATE requests SET state=?,result_json=?,artifact_ids_json=?,terminal_code=?,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (state, encoded, canonical_json(artifacts), terminal_code, timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )

    def mark_published(self, key: RequestKey, publication_ref: str, *, now: datetime | None = None) -> None:
        if not publication_ref or len(publication_ref) > 512:
            raise BrokerError("PUBLICATION_INVALID", "publication_ref is empty or too long")
        timestamp = utc_text(now)
        with self.transaction():
            row = self.get(key)
            if self.is_paused():
                raise BrokerError("PAUSED", "publication is paused locally")
            if row["state"] not in {"result_saved", "published"}:
                raise BrokerError("STATE_CONFLICT", "only durable results can be published")
            if row["publication_ref"] and row["publication_ref"] != publication_ref:
                raise BrokerError("PUBLICATION_CONFLICT", "request is already bound to a different publication")
            self._db.execute(
                "UPDATE requests SET state='published',publication_ref=?,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (publication_ref, timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )

    def mark_notification_pending(self, key: RequestKey, notification_ref: str, *, now: datetime | None = None) -> None:
        timestamp = utc_text(now)
        with self.transaction():
            row = self.get(key)
            if self.is_paused():
                raise BrokerError("PAUSED", "new handoff admission is paused locally")
            if row["state"] not in {"published", "notification_pending"}:
                raise BrokerError("STATE_CONFLICT", "notification requires a published result")
            self._db.execute(
                "UPDATE requests SET state='notification_pending',notification_ref=?,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (notification_ref, timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )

    def acknowledge(self, key: RequestKey, *, now: datetime | None = None) -> None:
        timestamp = utc_text(now)
        with self.transaction():
            row = self.get(key)
            if row["state"] not in {"published", "notification_pending", "acknowledged"}:
                raise BrokerError("STATE_CONFLICT", "acknowledgement requires publication")
            self._db.execute(
                "UPDATE requests SET state='acknowledged',updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                (timestamp, key.repository_id, key.mailbox_epoch, key.request_id),
            )

    def recover_running(self, registry: CapabilityRegistry, *, now: datetime | None = None) -> dict[str, int]:
        timestamp = utc_text(now)
        resumed = 0
        indeterminate = 0
        with self.transaction():
            rows = self._db.execute("SELECT repository_id,mailbox_epoch,request_id,capability,possible_effect FROM requests WHERE state='running'").fetchall()
            for row in rows:
                key = (row["repository_id"], row["mailbox_epoch"], row["request_id"])
                try:
                    spec = registry.get(row["capability"])
                except BrokerError:
                    spec = None
                if spec is not None and spec.retry_after_crash and not row["possible_effect"]:
                    self._db.execute("UPDATE requests SET state='ready',attempt_id=NULL,attempt_started_at=NULL,updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?", (timestamp, *key))
                    resumed += 1
                else:
                    self._db.execute("UPDATE requests SET state='indeterminate',terminal_code='CRASH_AFTER_START',updated_at=? WHERE repository_id=? AND mailbox_epoch=? AND request_id=?", (timestamp, *key))
                    indeterminate += 1
        return {"resumed": resumed, "indeterminate": indeterminate}


class Dispatcher:
    def __init__(self, journal: Journal, registry: CapabilityRegistry, *, authorize: Callable[[RequestKey], Decision] | None = None) -> None:
        self.journal = journal
        self.registry = registry
        self.authorize = authorize

    def execute(self, key: RequestKey, *, now: datetime | None = None) -> str:
        # Authorization and transition share the short DB transaction; neither the
        # human prompt nor capability handler runs while holding this writer lock.
        with self.journal.transaction():
            row = self.journal.get(key)
            spec = self.registry.get(row["capability"])
            if self.authorize is not None:
                decision = self.authorize(key)
                if not decision.permitted or decision.request_sha256 != row["input_sha256"]:
                    raise BrokerError("AUTHORITY_CHANGED", "current local authority does not permit execution")
            else:
                # Legacy low-level fake harnesses may omit authority. Requests
                # imported through the real control surface must never do so.
                bound = self.journal._db.execute("SELECT 1 FROM local_authority WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",
                    (key.repository_id, key.mailbox_epoch, key.request_id)).fetchone()
                if bound:
                    raise BrokerError("AUTHORITY_REQUIRED", "bound requests require current policy revalidation")
            attempt = self.journal.start(key, possible_effect=spec.mutating, now=now)
        context = ExecutionContext(key, attempt, lambda: self.journal.cancellation_requested(key))
        try:
            result = dict(spec.handler(row["request"], context))
            if context.cancellation_requested():
                self.journal.save_result(key, {"code": "CANCELLED", "artifact_ids": []}, state="cancelled", terminal_code="CANCELLED", now=now)
                return "cancelled"
            self.journal.save_result(key, result, now=now)
            return "result_saved"
        except BrokerError:
            raise
        except Exception as exc:
            self.journal.save_result(key, {"code": "HANDLER_FAILED", "error_type": type(exc).__name__, "artifact_ids": []}, state="failed", terminal_code="HANDLER_FAILED", now=now)
            return "failed"
