from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from interloc.broker import BrokerError, BrokerLock, Journal
from interloc.retention import RetentionError
from interloc.retention.recovery import mailbox_review, revocation_preview, revoke_pending
from tests.broker.test_broker import NOW, decision, request


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.journal = Journal(self.root / "broker" / "state.sqlite3")

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def add(self, action="confirm"):
        value = request()
        key, _ = self.journal.receive(42, value, decision(value, action), now=NOW)
        return key, value

    def test_revocation_is_reviewed_durable_and_preserves_replay_tombstone(self):
        key, value = self.add()
        plan = revocation_preview(self.journal, [key])
        with self.assertRaises(RetentionError):
            revoke_pending(self.journal, [key], reviewed_sha256="0" * 64)
        result = revoke_pending(self.journal, [key], reviewed_sha256=plan["review_sha256"])
        self.assertEqual(result["results"][0]["outcome"], "cancelled")
        self.assertFalse(result["running_effects_undone"])
        self.journal.close()
        self.journal = Journal(self.root / "broker" / "state.sqlite3")
        self.assertEqual(self.journal.receive(42, value, decision(value), now=NOW), (key, "cancelled"))
        new_plan = revocation_preview(self.journal, [key])
        self.assertEqual(revoke_pending(self.journal, [key], reviewed_sha256=new_plan["review_sha256"])["results"][0]["outcome"], "cancelled")

    def test_replayed_review_detects_state_changes(self):
        key, value = self.add()
        plan = revocation_preview(self.journal, [key])
        self.journal.approve(key, decision(value), now=NOW)
        with self.assertRaises(RetentionError) as ctx:
            revoke_pending(self.journal, [key], reviewed_sha256=plan["review_sha256"])
        self.assertEqual(ctx.exception.code, "REVIEW_STALE")
        self.assertEqual(self.journal.get(key)["state"], "ready")

    def test_already_started_result_and_mutations_are_not_undone(self):
        key, _ = self.add("allow")
        self.journal.start(key, possible_effect=True, now=NOW)
        plan = revocation_preview(self.journal, [key])
        result = revoke_pending(self.journal, [key], reviewed_sha256=plan["review_sha256"])
        self.assertEqual(result["results"][0]["outcome"], "reconcile_started_work")
        self.assertEqual(self.journal.get(key)["state"], "running")
        self.assertFalse(self.journal.cancellation_requested(key))

    def test_running_broker_owner_prevents_offline_changes(self):
        key, _ = self.add()
        with BrokerLock(self.journal.path.parent):
            with self.assertRaises(BrokerError) as ctx:
                revocation_preview(self.journal, [key])
            self.assertEqual(ctx.exception.code, "BROKER_ALREADY_RUNNING")

    def test_incident_scope_bounded_and_not_remote_paths(self):
        key, _ = self.add()
        for keys in ([key, key], [replace(key, repository_id=True)], [replace(key, request_id="../../x")]):
            with self.assertRaises(RetentionError):
                revocation_preview(self.journal, keys)
        def many():
            for _ in range(102):
                yield replace(key, request_id=str(uuid4()))
            raise AssertionError("reader must stop at 101 entries")
        with self.assertRaises(RetentionError):
            revocation_preview(self.journal, many())

    def test_mailbox_review_never_claims_history_erasure_or_executes_rotation(self):
        epoch, future = str(uuid4()), str(uuid4())
        plan = mailbox_review(repository_id=42, epoch=epoch, snapshot_sha="a" * 40,
                              active_bytes=100 * 1024 * 1024, oldest_age_seconds=7 * 86400, new_epoch=future)
        self.assertTrue(plan["size_review_due"])
        self.assertTrue(plan["age_review_due"])
        self.assertTrue(plan["requires_local_enrollment"])
        self.assertEqual(plan["historical_bytes"], "unknown")
        self.assertFalse(plan["history_erased"])
        self.assertEqual(plan["destructive_actions"], [])
        self.assertEqual(len(plan["review_sha256"]), 64)
        with self.assertRaises(RetentionError):
            mailbox_review(repository_id=42, epoch=epoch, snapshot_sha="a" * 40,
                           active_bytes=0, oldest_age_seconds=0, new_epoch=epoch)


if __name__ == "__main__":
    unittest.main()
