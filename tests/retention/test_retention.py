from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from interloc.retention import Limits, RetentionError, RetentionStore

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


class Crash(BaseException):
    pass


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="interloc retention ")
        self.root = Path(self.tmp.name) / "store"
        self.store_id = str(uuid4())
        self.now, self.free = NOW, 2**30
        self.limits = Limits(max_bytes=20, max_item_bytes=10, max_items=4, max_pending=2, max_records=8,
                             max_age_seconds=10, min_free_bytes=10, catalog_bytes=65536)
        self.store = self.open(initialize=True)

    def open(self, **kw):
        return RetentionStore(self.root, self.store_id, limits=self.limits, clock=lambda: self.now,
                              free_bytes=lambda: self.free, **kw)

    def reopen(self):
        self.store.close()
        self.store = self.open()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def code(self, expected, call, *args, **kw):
        with self.assertRaises(RetentionError) as caught:
            call(*args, **kw)
        self.assertEqual(caught.exception.code, expected)

    def put(self, value=b"synthetic", **kw):
        return self.store.put(value, kind="text", **kw)

    def test_defaults_and_strict_limit_bounds(self):
        Limits().validate()
        self.assertEqual(Limits().max_bytes, 256 * 1024 * 1024)
        self.assertEqual(Limits().max_age_seconds, 7 * 24 * 3600)
        for key, value in (("max_bytes", True), ("max_pending", 0), ("max_item_bytes", 21),
                           ("max_records", 3), ("min_free_bytes", -1), ("catalog_bytes", 100)):
            with self.subTest(key=key):
                self.code("INVALID_LIMITS", replace(self.limits, **{key: value}).validate)

    def test_exact_bytes_descriptor_and_opaque_inventory(self):
        data = b"\x1bSECRET"  # Retention does not pretend this is sanitized for publication.
        desc = self.put(data)
        self.assertEqual(self.store.read(desc["artifact_id"]), data)
        self.assertEqual(desc["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(desc["bytes"], len(data))
        self.assertTrue(desc["pending"])
        inventory = json.dumps(self.store.inventory())
        self.assertNotIn("SECRET", inventory)
        self.assertNotIn(str(self.root), inventory)
        self.assertNotIn("mailbox_path", desc)

    def test_same_id_reuses_bytes_and_expired_id_cannot_revive(self):
        desc = self.put()
        self.assertEqual(self.put(artifact_id=desc["artifact_id"]), desc)
        self.code("ARTIFACT_ID_COLLISION", self.put, b"changed", artifact_id=desc["artifact_id"])
        self.store.expire(desc["artifact_id"], desc["sha256"])
        outcome = self.put(artifact_id=desc["artifact_id"])
        self.assertEqual(outcome["state"], "expired")
        self.assertFalse(outcome["available"])
        self.assertEqual(self.store.status()["records"], 1)
        self.assertEqual(self.store.status()["bytes"], 0)

    def test_pending_age_and_quota_are_protected(self):
        one, two = self.put(b"A" * 10), self.put(b"B" * 10)
        self.now += timedelta(days=30)
        self.store.maintain()
        self.assertEqual(self.store.status()["bytes"], 20)
        self.code("PENDING_LIMIT", self.put, b"C")
        self.assertEqual(self.store.read(one["artifact_id"]), b"A" * 10)
        self.assertEqual(self.store.read(two["artifact_id"]), b"B" * 10)
        status = self.store.status()
        self.assertFalse(status["collection_allowed"])
        self.assertFalse(status["publication_allowed"])
        self.assertEqual(status["gaps"], 1)
        self.assertEqual(status["dropped_bytes"], 1)

    def test_unprotected_oldest_evicted_for_byte_quota(self):
        one = self.put(b"A" * 10, pending=False)
        self.now += timedelta(seconds=1)
        two = self.put(b"B" * 10)
        self.now += timedelta(seconds=1)
        three = self.put(b"C" * 10)
        self.assertEqual(self.store.describe(one["artifact_id"])["state"], "evicted")
        self.assertTrue(self.store.describe(two["artifact_id"])["available"])
        self.assertTrue(self.store.describe(three["artifact_id"])["available"])
        self.assertEqual(self.store.status()["bytes"], 20)
        self.assertEqual(self.store.status()["records"], 3)

    def test_ack_makes_artifact_age_eligible(self):
        desc = self.put()
        self.now += timedelta(seconds=11)
        self.store.maintain()
        self.assertTrue(self.store.describe(desc["artifact_id"])["available"])
        self.store.acknowledge(desc["artifact_id"], desc["sha256"])
        self.store.maintain()
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "expired")

    def test_explicit_deadline_protects_until_expiry_then_unavailable(self):
        desc = self.put(expires_at=self.now + timedelta(seconds=1))
        self.now += timedelta(seconds=2)
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "expiry_due")
        self.code("ARTIFACT_UNAVAILABLE", self.store.read, desc["artifact_id"])
        self.reopen()
        self.store.maintain()
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "expired")

    def test_local_expiry_requires_exact_review_digest(self):
        desc = self.put()
        self.code("REVIEW_STALE", self.store.expire, desc["artifact_id"], "0" * 64)
        self.code("REVIEW_STALE", self.store.acknowledge, desc["artifact_id"], "0" * 64)
        self.assertTrue(self.store.describe(desc["artifact_id"])["available"])
        self.store.expire(desc["artifact_id"], desc["sha256"])
        self.reopen()
        self.assertEqual(self.store.expire(desc["artifact_id"], desc["sha256"])["state"], "expired")

    def test_object_count_caps_zero_byte_artifacts(self):
        for _ in range(4):
            self.put(b"", pending=False)
        self.put(b"", pending=False)
        status = self.store.status()
        self.assertEqual(status["items"], 4)
        self.assertEqual(status["bytes"], 0)
        self.assertEqual(status["records"], 5)

    def test_tombstone_budget_blocks_instead_of_forgetting_ids(self):
        for _ in range(8):
            desc = self.put()
            self.store.expire(desc["artifact_id"], desc["sha256"])
        self.code("CATALOG_LIMIT", self.put)
        self.reopen()
        self.assertEqual(self.store.status()["records"], 8)
        self.assertEqual(self.store.status()["bytes"], 0)
        self.code("CAPACITY_REVIEW_REQUIRED", self.store.resume, self.store.status()["revision"])

    def test_low_disk_blocks_without_accepting_or_deleting_pending(self):
        desc = self.put()
        self.free = 5
        self.assertFalse(self.store.status()["publication_allowed"])
        self.code("DISK_LOW", self.put)
        self.assertEqual(self.store.status()["records"], 1)
        self.assertEqual(self.store.read(desc["artifact_id"]), b"synthetic")
        self.free = 2**30
        self.reopen()
        self.assertTrue(self.store.status()["paused"])
        self.store.resume(self.store.status()["revision"])
        self.assertTrue(self.store.status()["collection_allowed"])

    def test_io_error_sanitized_and_recovered_as_explicit_gap(self):
        with patch("interloc.retention.core.os.fsync", side_effect=OSError(errno.ENOSPC, "secret/local/path")):
            self.code("STORAGE_IO", self.put)
        self.assertTrue(self.store.status()["paused"])
        self.reopen()
        self.assertEqual(self.store.status()["items"], 1)  # Complete staged bytes can be recovered.
        self.assertEqual(self.store.inventory()["artifacts"][0]["state"], "available")
        self.assertTrue(self.store.status()["paused"])

    def test_reservation_crash_leaves_failed_tombstone_not_dangling_file(self):
        def fault(point):
            if point == "after_reservation":
                raise Crash
        self.store.fault = fault
        with self.assertRaises(Crash):
            self.put()
        self.reopen()
        desc = self.store.inventory()["artifacts"][0]
        self.assertFalse(desc["available"])
        self.assertEqual(desc["state"], "failed")
        self.assertEqual(desc["reason"], "INTERRUPTED_WRITE")
        self.assertEqual(self.store.status()["items"], 0)
        self.assertEqual(self.store.status()["gaps"], 1)

    def test_partial_write_recovery_deletes_only_known_partial(self):
        key = str(uuid4())
        def fault(point):
            if point == "after_reservation":
                (self.root / "staging" / (key + ".part")).write_bytes(b"partial")
                raise Crash
        self.store.fault = fault
        with self.assertRaises(Crash):
            self.put(b"A" * 10, artifact_id=key)
        self.reopen()
        self.assertEqual(self.store.describe(key)["state"], "failed")
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_complete_write_and_rename_crash_recover_exact_pending_bytes(self):
        for point in ("after_write", "after_rename"):
            with self.subTest(point=point):
                def fault(current):
                    if current == point:
                        raise Crash
                self.store.fault = fault
                key = str(uuid4())
                with self.assertRaises(Crash):
                    self.put(b"A", artifact_id=key)
                self.reopen()
                self.assertEqual(self.store.read(key), b"A")
                self.assertTrue(self.store.describe(key)["pending"])

    def test_eviction_intent_and_unlink_crash_recover_expired_tombstone(self):
        for point in ("after_eviction_intent", "after_unlink"):
            with self.subTest(point=point):
                desc = self.put()
                def fault(current):
                    if current == point:
                        raise Crash
                self.store.fault = fault
                with self.assertRaises(Crash):
                    self.store.expire(desc["artifact_id"], desc["sha256"])
                self.assertFalse(self.store.describe(desc["artifact_id"])["available"])
                self.reopen()
                self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "expired")
                self.assertEqual(self.store.status()["bytes"], 0)

    def test_deleted_file_is_never_described_as_available(self):
        desc = self.put()
        (self.root / "objects" / (desc["artifact_id"] + ".bin")).unlink()
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "missing")
        self.code("ARTIFACT_UNAVAILABLE", self.store.read, desc["artifact_id"])
        self.reopen()
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "missing")
        self.assertTrue(self.store.status()["paused"])

    def test_changed_file_is_not_deleted_or_exported(self):
        desc = self.put()
        final = self.root / "objects" / (desc["artifact_id"] + ".bin")
        final.write_bytes(b"malicious")
        self.code("ARTIFACT_CHANGED", self.store.read, desc["artifact_id"])
        self.code("ARTIFACT_CHANGED", self.store.expire, desc["artifact_id"], desc["sha256"])
        self.assertEqual(final.read_bytes(), b"malicious")

    def test_unknown_file_stops_cleanup_before_age_eviction(self):
        desc = self.put(pending=False)
        self.now += timedelta(seconds=11)
        unknown = self.root / "objects" / (str(uuid4()) + ".bin")
        unknown.write_bytes(b"do not touch")
        self.code("UNKNOWN_STORAGE", self.store.maintain)
        self.assertTrue((self.root / "objects" / (desc["artifact_id"] + ".bin")).exists())
        self.assertEqual(unknown.read_bytes(), b"do not touch")
        self.code("UNKNOWN_STORAGE", self.store.resume, 0)

    def test_tombstone_with_reappeared_bytes_requires_review(self):
        desc = self.put()
        self.store.expire(desc["artifact_id"], desc["sha256"])
        (self.root / "objects" / (desc["artifact_id"] + ".bin")).write_bytes(b"synthetic")
        self.code("CATALOG_INCONSISTENT", self.store.reconcile)

    def test_changed_owner_and_unknown_root_files_are_not_adopted(self):
        owner = self.root / "owner.json"
        original = owner.read_bytes()
        owner.write_text(json.dumps({"schema_version": 1, "store_id": str(uuid4())}))
        self.code("OWNERSHIP_MISMATCH", self.store.status)
        owner.write_bytes(original)
        (self.root / "unknown.txt").write_bytes(b"keep")
        self.code("UNKNOWN_STORAGE", self.store.maintain)
        self.assertEqual((self.root / "unknown.txt").read_bytes(), b"keep")

    def test_single_owner_lock_and_non_growing_lockfile(self):
        self.code("STORE_BUSY", self.open)
        size = (self.root / "owner.lock").stat().st_size
        for _ in range(5):
            self.reopen()
        self.assertEqual((self.root / "owner.lock").stat().st_size, size)

    def test_initialize_never_adopts_existing_directory(self):
        with self.assertRaises(FileExistsError):
            self.open(initialize=True)
        self.assertEqual(self.store.status()["records"], 0)

    def test_catalog_missing_and_newer_version_fail_closed(self):
        self.store._db.execute("PRAGMA user_version=99")
        self.store.close()
        self.code("CATALOG_VERSION", self.open)
        (self.root / "catalog.sqlite3").unlink()
        self.code("CATALOG_MISSING", self.open)

    def test_inventory_bounded_pagination_and_no_content(self):
        for _ in range(4):
            self.put(b"test", pending=False)
        first = self.store.inventory(limit=2)
        self.assertEqual(len(first["artifacts"]), 2)
        self.assertIsNotNone(first["next"])
        second = self.store.inventory(after=first["next"], limit=2)
        self.assertEqual(len(second["artifacts"]), 2)
        self.assertIsNone(second["next"])
        self.code("INVALID_LIMIT", self.store.inventory, limit=101)
        self.code("INVALID_ID", self.store.inventory, after="../../secret")

    def test_incident_pause_is_durable_and_blocks_automatic_cleanup(self):
        desc = self.put(pending=False)
        paused = self.store.pause_incident()
        self.now += timedelta(seconds=11)
        self.code("INCIDENT_PAUSED", self.store.maintain)
        self.reopen()
        self.assertTrue(self.store.status()["incident"])
        self.assertTrue((self.root / "objects" / (desc["artifact_id"] + ".bin")).exists())
        self.code("REVIEW_STALE", self.store.resume, paused["revision"] - 1)
        self.store.resume(self.store.status()["revision"])
        self.store.maintain()
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "expired")

    def test_remote_like_paths_boolean_flags_and_oversize_rejected(self):
        self.code("INVALID_ID", self.put, artifact_id="C:\\private")
        self.code("INVALID_ARTIFACT", self.put, pending=1)
        self.code("INVALID_ARTIFACT", self.put, request_ref="/personal/secret")
        self.code("ITEM_LIMIT", self.put, b"x" * 11)
        self.code("ALREADY_EXPIRED", self.put, expires_at=NOW)
        self.code("INVALID_CLOCK", self.put, expires_at=datetime(2026, 9, 13))
        self.assertEqual(self.store.status()["records"], 0)

    def test_symlinks_hardlinks_and_project_roots_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.write_bytes(b"synthetic")
        desc = self.put()
        target = self.root / "objects" / (desc["artifact_id"] + ".bin")
        target.unlink()
        try:
            os.link(outside, target)
        except OSError:
            self.skipTest("hardlinks not available on this filesystem")
        self.code("UNSAFE_FILE", self.store.reconcile)
        self.assertEqual(outside.read_bytes(), b"synthetic")
        target.unlink()
        project = Path(self.tmp.name) / "project"
        project.mkdir()
        (project / ".git").write_text("gitdir: synthetic")
        self.code("UNSAFE_ROOT", RetentionStore, project / "store", self.store_id, initialize=True)

    def test_strict_marker_rejects_duplicate_keys_and_boolean_version(self):
        owner = self.root / "owner.json"
        for data in (json.dumps({"schema_version": True, "store_id": self.store_id}).encode(),
                     ('{"schema_version":1,"schema_version":1,"store_id":"' + self.store_id + '"}').encode()):
            owner.write_bytes(data)
            self.code("OWNERSHIP_MISMATCH", self.store.status)
        self.assertTrue(self.store._memory_block)

    def test_complete_bytes_are_not_silently_replaced_by_partial_after_crash(self):
        desc = self.put()
        self.store._db.execute("UPDATE objects SET state='writing' WHERE id=?", (desc["artifact_id"],))
        (self.root / "staging" / (desc["artifact_id"] + ".part")).write_bytes(b"partial")
        self.code("ARTIFACT_CHANGED", self.store.reconcile)
        self.assertEqual((self.root / "objects" / (desc["artifact_id"] + ".bin")).read_bytes(), b"synthetic")

    def test_catalog_disk_limit_is_a_real_sqlite_failure_without_tombstone_loss(self):
        self.store.close()
        self.limits = replace(self.limits, max_records=1000)
        self.store = self.open()
        first = self.put()
        self.store.expire(first["artifact_id"], first["sha256"])
        failure = None
        for _ in range(999):
            try:
                desc = self.put()
                self.store.expire(desc["artifact_id"], desc["sha256"])
            except RetentionError as exc:
                failure = exc.code
                break
        self.assertEqual(failure, "STORAGE_IO")
        self.assertTrue(self.store.status()["paused"])
        self.assertEqual(self.store.describe(first["artifact_id"])["state"], "expired")
        self.assertLessEqual((self.root / "catalog.sqlite3").stat().st_size, self.limits.catalog_bytes)
        self.assertLessEqual(self.store.status()["bytes"], self.limits.max_bytes)

    def test_owned_payload_symlink_is_rejected_without_following(self):
        desc = self.put()
        final = self.root / "objects" / (desc["artifact_id"] + ".bin")
        outside = Path(self.tmp.name) / "private"
        outside.write_bytes(b"private")
        final.unlink()
        try:
            final.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation requires Windows developer privileges")
        self.code("UNSAFE_FILE", self.store.read, desc["artifact_id"])
        self.assertEqual(outside.read_bytes(), b"private")

    def test_status_read_reports_age_expiry_without_deleting(self):
        desc = self.put(pending=False)
        self.now += timedelta(seconds=11)
        self.assertEqual(self.store.describe(desc["artifact_id"])["state"], "expiry_due")
        self.assertTrue((self.root / "objects" / (desc["artifact_id"] + ".bin")).exists())

    def test_failed_sqlite_reservation_never_creates_payload(self):
        self.store._db.execute("""CREATE TRIGGER reject_insert BEFORE INSERT ON objects
            BEGIN SELECT RAISE(ABORT, 'synthetic'); END""")
        self.code("STORAGE_IO", self.put)
        self.assertEqual(self.store.status()["records"], 0)
        self.assertEqual(list((self.root / "objects").iterdir()), [])
        self.assertEqual(list((self.root / "staging").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
