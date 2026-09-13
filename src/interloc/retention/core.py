"""Bounded, explicitly owned local storage. No transport or execution authority.

A single process owns the store; short SQLite transactions record file intents.
File deletion is never inferred from an unrecognised filename or remote path.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import threading
from typing import Callable
from uuid import UUID, uuid4

from interloc.protocol import ProtocolError, strict_loads


class RetentionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        # Never include an input path, file contents or a raw OS exception.
        super().__init__(code)


def identifier(value: str) -> str:
    try:
        result = str(UUID(value))
        if result != value:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise RetentionError("INVALID_ID") from exc


def timestamp(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RetentionError("INVALID_CLOCK")
    return int(value.timestamp() * 1_000_000)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 256 * 1024 * 1024
    max_item_bytes: int = 16 * 1024 * 1024
    max_items: int = 10_000
    max_pending: int = 100
    max_records: int = 100_000
    max_age_seconds: int = 7 * 24 * 3600
    min_free_bytes: int = 64 * 1024 * 1024
    catalog_bytes: int = 32 * 1024 * 1024

    def validate(self) -> None:
        values = asdict(self)
        if any(type(v) is not int or v < (0 if k == "min_free_bytes" else 1)
               for k, v in values.items()):
            raise RetentionError("INVALID_LIMITS")
        if (self.max_bytes > 2**30 or self.max_item_bytes > min(self.max_bytes, 16 * 1024 * 1024)
                or self.max_items > 10_000 or not self.max_pending <= min(self.max_items, 1000)
                or not self.max_items <= self.max_records <= 100_000
                or self.max_age_seconds > 365 * 24 * 3600 or self.min_free_bytes > 2**40
                or not 64 * 1024 <= self.catalog_bytes <= 128 * 1024 * 1024):
            raise RetentionError("INVALID_LIMITS")


class _OwnerLock:
    """Non-growing lock file, retained inside the already verified store."""
    def __init__(self, path: Path) -> None:
        self.handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.handle.fileno()).st_size == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.handle.close()
            raise RetentionError("STORE_BUSY") from None

    def close(self) -> None:
        self.handle.close()  # Closing releases the OS lock on both platforms.


def operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._mutex:
            if self._closed:
                raise RetentionError("STORE_CLOSED")
            try:
                self._verify_owner()
                return method(self, *args, **kwargs)
            except RetentionError as exc:
                if exc.code in {"UNSAFE_ROOT", "UNSAFE_FILE", "UNKNOWN_STORAGE", "OWNERSHIP_MISMATCH", "CATALOG_INCONSISTENT", "INVALID_FREE_SPACE"}:
                    self._block(exc.code)
                raise
            except (OSError, sqlite3.Error):
                self._block("STORAGE_IO")
                raise RetentionError("STORAGE_IO") from None
    return guarded


class RetentionStore:
    """One locally enrolled store. Call close(), or use a context manager.

    Limits apply to owned payload bytes, object counts and catalog size; they do
    not pretend to cap the broker journal or unrelated files on the same disk.
    Consumers must check status/read before publishing. IL-010 owns composition.
    """
    def __init__(self, root: Path, store_id: str, *, initialize: bool = False,
                 limits: Limits = Limits(), clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 free_bytes: Callable[[], int] | None = None,
                 fault: Callable[[str], None] = lambda point: None) -> None:
        limits.validate()
        self.root, self.store_id, self.limits = Path(root), identifier(store_id), limits
        self.clock, self.free_bytes, self.fault = clock, free_bytes or (lambda: shutil.disk_usage(self.root).free), fault
        self._mutex, self._closed, self._memory_block = threading.RLock(), False, None
        self._db, self._lock = None, None
        self._check_root()
        try:
            if initialize:
                self.root.mkdir(mode=0o700, parents=False, exist_ok=False)
                (self.root / "objects").mkdir(mode=0o700)
                (self.root / "staging").mkdir(mode=0o700)
                with (self.root / "owner.json").open("xb") as handle:
                    handle.write(json.dumps({"schema_version": 1, "store_id": self.store_id}).encode())
                    handle.flush()
                    os.fsync(handle.fileno())
            self._verify_owner()
            self._lock = _OwnerLock(self.root / "owner.lock")
            database = self.root / "catalog.sqlite3"
            if not initialize and not database.is_file():
                raise RetentionError("CATALOG_MISSING")
            self._db = sqlite3.connect(database, isolation_level=None, check_same_thread=False, timeout=2)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            page = self._db.execute("PRAGMA page_size").fetchone()[0]
            if self._db.execute("PRAGMA page_count").fetchone()[0] * page > limits.catalog_bytes:
                raise RetentionError("CATALOG_LIMIT")
            self._db.execute(f"PRAGMA max_page_count={limits.catalog_bytes // page}")
            if initialize:
                with self._transaction():
                    self._db.execute("""CREATE TABLE objects (
                        id TEXT PRIMARY KEY, sha TEXT NOT NULL, size INTEGER NOT NULL,
                        kind TEXT NOT NULL, created INTEGER NOT NULL, deadline INTEGER,
                        pending INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
                        request_ref TEXT NOT NULL)""")
                    self._db.execute("CREATE INDEX active_objects ON objects(state,created)")
                    self._db.execute("""CREATE TABLE control (
                        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                        paused TEXT, incident INTEGER NOT NULL, gaps INTEGER NOT NULL,
                        dropped_bytes INTEGER NOT NULL)""")
                    self._db.execute("INSERT INTO control VALUES(1,0,NULL,0,0,0)")
                    self._db.execute("PRAGMA user_version=1")
            if self._db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RetentionError("CATALOG_VERSION")
            if self._db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise RetentionError("CATALOG_INCONSISTENT")
            self.reconcile()
        except BaseException:
            self.close()
            raise

    def _check_root(self) -> None:
        if not self.root.is_absolute():
            raise RetentionError("UNSAFE_ROOT")
        for path in (self.root, *self.root.parents):
            if path.is_symlink() or path.is_junction() or (path / ".git").exists():
                raise RetentionError("UNSAFE_ROOT")

    @staticmethod
    def _regular(path: Path) -> None:
        info = path.lstat()
        if (path.is_symlink() or path.is_junction() or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1):
            raise RetentionError("UNSAFE_FILE")

    def _verify_owner(self) -> None:
        self._check_root()
        allowed = {"objects", "staging", "owner.json", "owner.lock", "catalog.sqlite3", "catalog.sqlite3-journal"}
        for path in self.root.iterdir():
            if path.name not in allowed:
                raise RetentionError("UNKNOWN_STORAGE")
            if path.name in {"objects", "staging"}:
                if path.is_symlink() or path.is_junction() or not path.is_dir():
                    raise RetentionError("UNSAFE_FILE")
            else:
                self._regular(path)
        owner = self.root / "owner.json"
        self._regular(owner)
        with owner.open("rb") as handle:
            data = handle.read(1025)
        expected = {"schema_version": 1, "store_id": self.store_id}
        try:
            parsed = strict_loads(data, max_bytes=1024)
            if parsed != expected or type(parsed.get("schema_version")) is not int:
                raise ValueError
        except (ValueError, UnicodeError, ProtocolError):
            raise RetentionError("OWNERSHIP_MISMATCH") from None
        if not all((self.root / p).is_dir() for p in ("objects", "staging")):
            raise RetentionError("OWNERSHIP_MISMATCH")

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def _block(self, code: str, dropped: int = 0) -> None:
        self._memory_block = code
        try:
            with self._transaction():
                self._db.execute("""UPDATE control SET paused=?,revision=revision+1,
                    gaps=MIN(gaps+1,2147483647),dropped_bytes=MIN(dropped_bytes+?,9223372036854775807) WHERE id=1""", (code, dropped))
        except (sqlite3.Error, AttributeError):
            pass  # Disk full may prevent recording; the in-memory gate still fails closed.

    def _set(self, key: str, state: str, reason: str = "") -> None:
        with self._transaction():
            self._db.execute("UPDATE objects SET state=?,reason=? WHERE id=?", (state, reason, key))
            self._db.execute("UPDATE control SET revision=revision+1 WHERE id=1")

    def _row(self, key: str) -> dict:
        row = self._db.execute("SELECT * FROM objects WHERE id=?", (identifier(key),)).fetchone()
        if row is None:
            raise RetentionError("ARTIFACT_UNKNOWN")
        return dict(row)

    def _paths(self, key: str) -> tuple[Path, Path]:
        identifier(key)
        return self.root / "objects" / (key + ".bin"), self.root / "staging" / (key + ".part")

    def _matches(self, path: Path, row: dict) -> bool:
        self._regular(path)
        if path.stat().st_size != row["size"]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest() == row["sha"]

    def _owned_files(self) -> None:
        # Complete the bounded ownership scan before any cleanup is attempted.
        count = 0
        for folder, suffix in (("objects", ".bin"), ("staging", ".part")):
            with os.scandir(self.root / folder) as entries:
                for item in entries:
                    count += 1
                    if count > self.limits.max_items * 2:
                        raise RetentionError("UNKNOWN_STORAGE")
                    key = item.name.removesuffix(suffix)
                    if item.name != key + suffix:
                        raise RetentionError("UNKNOWN_STORAGE")
                    try:
                        row = self._row(key)
                    except RetentionError:
                        raise RetentionError("UNKNOWN_STORAGE") from None
                    self._regular(Path(item.path))
                    allowed = {"writing", "evicting"} if folder == "staging" else {"writing", "available", "evicting"}
                    if row["state"] not in allowed:
                        raise RetentionError("CATALOG_INCONSISTENT")

    @operation
    def reconcile(self) -> dict:
        try:
            self._owned_files()
        except RetentionError as exc:
            self._block(exc.code)
            raise
        rows = self._db.execute("SELECT * FROM objects WHERE state IN ('writing','available','evicting') ORDER BY id LIMIT ?", (self.limits.max_items + 1,)).fetchall()
        if len(rows) > self.limits.max_items:
            self._block("CATALOG_INCONSISTENT")
            raise RetentionError("CATALOG_INCONSISTENT")
        for original in rows:
            row = dict(original)
            final, partial = self._paths(row["id"])
            if row["state"] == "evicting":
                self._finish_eviction(row)
            elif row["state"] == "writing":
                if final.exists():
                    if partial.exists() or not self._matches(final, row):
                        self._block("ARTIFACT_CHANGED")
                        raise RetentionError("ARTIFACT_CHANGED")
                    self._set(row["id"], "available")
                elif partial.exists() and self._matches(partial, row):
                    os.replace(partial, final)
                    self._set(row["id"], "available")
                else:
                    if partial.exists():
                        self._regular(partial)
                        partial.unlink()  # This exact incomplete file has a durable reservation.
                    self._set(row["id"], "failed", "INTERRUPTED_WRITE")
                    self._block("INTERRUPTED_WRITE", row["size"])
            elif not final.exists():
                self._set(row["id"], "missing", "ARTIFACT_MISSING")
                self._block("ARTIFACT_MISSING", row["size"])
            elif not self._matches(final, row):
                self._block("ARTIFACT_CHANGED")
                raise RetentionError("ARTIFACT_CHANGED")
        return self._status()

    def _finish_eviction(self, row: dict) -> None:
        final, partial = self._paths(row["id"])
        if partial.exists():
            self._block("CATALOG_INCONSISTENT")
            raise RetentionError("CATALOG_INCONSISTENT")
        if final.exists():
            if not self._matches(final, row):
                self._block("ARTIFACT_CHANGED")
                raise RetentionError("ARTIFACT_CHANGED")
            final.unlink()
        self.fault("after_unlink")
        self._set(row["id"], row["reason"], row["reason"])

    def _evict(self, row: dict, state: str) -> None:
        self._set(row["id"], "evicting", state)
        self.fault("after_eviction_intent")
        self._finish_eviction({**row, "state": "evicting", "reason": state})

    def _status(self) -> dict:
        ctl = dict(self._db.execute("SELECT * FROM control WHERE id=1").fetchone())
        totals = self._db.execute("""SELECT COUNT(*) AS records,
            COALESCE(SUM(CASE WHEN state IN ('writing','available','evicting') THEN size ELSE 0 END),0) AS bytes,
            COALESCE(SUM(state IN ('writing','available','evicting')),0) AS items,
            COALESCE(SUM(pending=1 AND state IN ('writing','available','evicting')),0) AS pending FROM objects""").fetchone()
        reason = self._memory_block or ctl["paused"]
        free = self.free_bytes()
        if type(free) is not int or free < 0:
            raise RetentionError("INVALID_FREE_SPACE")
        if not reason and free < self.limits.min_free_bytes:
            reason = "DISK_LOW"
        return {"store_id": self.store_id, **dict(totals), "revision": ctl["revision"],
                "paused": reason is not None, "reason": reason, "incident": bool(ctl["incident"]),
                "collection_allowed": reason is None, "publication_allowed": reason is None,
                "gaps": ctl["gaps"], "dropped_bytes": ctl["dropped_bytes"]}

    @operation
    def status(self) -> dict:
        self._owned_files()
        return self._status()

    @operation
    def maintain(self, *, required_bytes: int = 0, required_items: int = 0) -> dict:
        if (type(required_bytes) is not int or not 0 <= required_bytes <= self.limits.max_item_bytes
                or type(required_items) is not int or required_items not in (0, 1)):
            raise RetentionError("INVALID_RESERVATION")
        self.reconcile()
        if self._status()["incident"]:
            raise RetentionError("INCIDENT_PAUSED")
        now = timestamp(self.clock())
        for raw in self._db.execute("SELECT * FROM objects WHERE state='available' ORDER BY created,id").fetchall():
            row = dict(raw)
            if ((row["deadline"] is not None and row["deadline"] <= now)
                    or (not row["pending"] and row["created"] + self.limits.max_age_seconds * 1_000_000 <= now)):
                self._evict(row, "expired")
        for raw in self._db.execute("SELECT * FROM objects WHERE state='available' AND pending=0 ORDER BY created,id").fetchall():
            status = self._status()
            if (status["bytes"] + required_bytes <= self.limits.max_bytes
                    and status["items"] + required_items <= self.limits.max_items):
                break
            self._evict(dict(raw), "evicted")
        return self._status()

    @operation
    def put(self, data: bytes, *, kind: str, pending: bool = True, artifact_id: str | None = None,
            expires_at: datetime | None = None, request_ref: str = "") -> dict:
        if (not isinstance(data, bytes) or kind not in {"raw", "text", "attachment"}
                or type(pending) is not bool or not isinstance(request_ref, str)
                or len(request_ref) > 64 or any(c not in "0123456789abcdef-" for c in request_ref)):
            raise RetentionError("INVALID_ARTIFACT")
        if len(data) > self.limits.max_item_bytes:
            raise RetentionError("ITEM_LIMIT")
        key = identifier(artifact_id) if artifact_id else str(uuid4())
        sha = hashlib.sha256(data).hexdigest()
        row = self._db.execute("SELECT * FROM objects WHERE id=?", (key,)).fetchone()
        if row:
            if row["sha"] != sha or row["kind"] != kind or row["request_ref"] != request_ref:
                raise RetentionError("ARTIFACT_ID_COLLISION")
            return self.describe(key)  # Never recreate an evicted/expired ID or extend consent.
        deadline = timestamp(expires_at) if expires_at is not None else None
        now = timestamp(self.clock())
        if deadline is not None and deadline <= now:
            raise RetentionError("ALREADY_EXPIRED")
        status = self.maintain(required_bytes=len(data), required_items=1)
        code = status["reason"]
        if status["records"] >= self.limits.max_records:
            code = "CATALOG_LIMIT"
        elif pending and status["pending"] >= self.limits.max_pending:
            code = "PENDING_LIMIT"
        elif status["bytes"] + len(data) > self.limits.max_bytes or status["items"] + 1 > self.limits.max_items:
            code = "CAPACITY_PROTECTED"
        elif self.free_bytes() < self.limits.min_free_bytes + len(data) + 65536:
            code = "DISK_LOW"
        if code:
            self._block(code, len(data))
            raise RetentionError(code)
        with self._transaction():
            self._db.execute("INSERT INTO objects VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, sha, len(data), kind, now, deadline, int(pending), "writing", "", request_ref))
            self._db.execute("UPDATE control SET revision=revision+1 WHERE id=1")
        self.fault("after_reservation")
        final, partial = self._paths(key)
        with partial.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self.fault("after_write")
        os.replace(partial, final)
        self.fault("after_rename")
        self._set(key, "available")
        return self.describe(key)

    @operation
    def describe(self, key: str) -> dict:
        row = self._row(key)
        if row["state"] == "available":
            final, _ = self._paths(key)
            if not final.exists():
                self._set(key, "missing", "ARTIFACT_MISSING")
                self._block("ARTIFACT_MISSING", row["size"])
                row = self._row(key)
            elif not self._matches(final, row):
                self._block("ARTIFACT_CHANGED")
                raise RetentionError("ARTIFACT_CHANGED")
            elif ((row["deadline"] is not None and row["deadline"] <= timestamp(self.clock()))
                    or (not row["pending"] and row["created"] + self.limits.max_age_seconds * 1_000_000 <= timestamp(self.clock()))):
                # A status read never silently deletes; it reports unavailable immediately.
                row = {**row, "state": "expiry_due"}
        return {"artifact_id": key, "sha256": row["sha"], "bytes": row["size"], "kind": row["kind"],
                "state": row["state"], "available": row["state"] == "available", "pending": bool(row["pending"]),
                "request_ref": row["request_ref"], "reason": row["reason"]}

    @operation
    def read(self, key: str) -> bytes:
        desc = self.describe(key)
        if not desc["available"]:
            raise RetentionError("ARTIFACT_UNAVAILABLE")
        final, _ = self._paths(key)
        with final.open("rb") as handle:
            data = handle.read(self.limits.max_item_bytes + 1)
        if len(data) != desc["bytes"] or hashlib.sha256(data).hexdigest() != desc["sha256"]:
            self._block("ARTIFACT_CHANGED")
            raise RetentionError("ARTIFACT_CHANGED")
        return data

    @operation
    def acknowledge(self, key: str, expected_sha256: str) -> None:
        row = self._row(key)
        if row["sha"] != expected_sha256:
            raise RetentionError("REVIEW_STALE")
        if row["state"] != "available":
            raise RetentionError("ARTIFACT_UNAVAILABLE")
        with self._transaction():
            self._db.execute("UPDATE objects SET pending=0 WHERE id=?", (key,))
            self._db.execute("UPDATE control SET revision=revision+1 WHERE id=1")

    @operation
    def expire(self, key: str, expected_sha256: str) -> dict:
        """Explicit local reviewed expiry, including protected attachments."""
        self._owned_files()
        row = self._row(key)
        if row["sha"] != expected_sha256:
            raise RetentionError("REVIEW_STALE")
        if row["state"] == "available":
            self._evict(row, "expired")
        elif row["state"] != "expired":
            raise RetentionError("ARTIFACT_UNAVAILABLE")
        return self.describe(key)

    @operation
    def inventory(self, *, after: str = "", limit: int = 100) -> dict:
        if after:
            identifier(after)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise RetentionError("INVALID_LIMIT")
        rows = self._db.execute("SELECT id FROM objects WHERE id>? ORDER BY id LIMIT ?", (after, limit + 1)).fetchall()
        entries = [self.describe(row["id"]) for row in rows[:limit]]
        return {"store_id": self.store_id, "revision": self._status()["revision"], "artifacts": entries,
                "next": entries[-1]["artifact_id"] if len(rows) > limit else None}

    @operation
    def pause_incident(self) -> dict:
        self._block("INCIDENT_PAUSED")
        with self._transaction():
            self._db.execute("UPDATE control SET incident=1 WHERE id=1")
        return self._status()

    @operation
    def resume(self, expected_revision: int) -> dict:
        """Explicit local review. Reconcile first; never clear unknown ownership."""
        self.reconcile()
        status = self._status()
        if type(expected_revision) is not int or expected_revision != status["revision"]:
            raise RetentionError("REVIEW_STALE")
        if (self.free_bytes() < self.limits.min_free_bytes + 65536
                or status["bytes"] > self.limits.max_bytes or status["items"] > self.limits.max_items
                or status["records"] >= self.limits.max_records):
            raise RetentionError("CAPACITY_REVIEW_REQUIRED")
        with self._transaction():
            self._db.execute("UPDATE control SET paused=NULL,incident=0,revision=revision+1 WHERE id=1")
        self._memory_block = None
        return self._status()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
