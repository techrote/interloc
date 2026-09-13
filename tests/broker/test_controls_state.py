"""Schema-v2 migration and transactional control regressions."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from interloc.broker import BrokerError, Journal
from tests.broker.test_broker import NOW, decision, request


class ControlStateTests(unittest.TestCase):
    def test_v1_upgrade_retains_terminal_replay_rows_and_never_grants_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            journal = Journal(path)
            value = request()
            key, _ = journal.receive(42, value, decision(value), now=NOW)
            journal.cancel(key, now=NOW)
            # Drop only the v2 additions to construct an exact v1 fixture.
            with journal.transaction():
                journal._db.execute("DROP TABLE local_authority")
                journal._db.execute("DROP TABLE control_state")
                journal._db.execute("PRAGMA user_version=1")
            journal.close()
            upgraded = Journal(path)
            try:
                self.assertEqual(upgraded._db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(upgraded.receive(42, value, decision(value), now=NOW), (key, "cancelled"))
                with self.assertRaises(BrokerError) as caught:
                    upgraded.authority(key)
                self.assertEqual(caught.exception.code, "AUTHORITY_MISSING")
                self.assertFalse(upgraded.is_paused())
            finally:
                upgraded.close()

    def test_intake_and_binding_save_are_atomic_on_disk_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "state.sqlite3")
            try:
                value = request()
                with patch("interloc.broker.core.canonical_json", side_effect=[b"{}", sqlite3.OperationalError("synthetic disk full")]):
                    with self.assertRaises(sqlite3.OperationalError):
                        journal.receive(42, value, decision(value), now=NOW, binding={"synthetic": True})
                self.assertEqual(journal.status()["counts"], {})
                self.assertEqual(journal._db.execute("SELECT COUNT(*) FROM local_authority").fetchone()[0], 0)
            finally:
                journal.close()

    def test_two_connections_cannot_admit_past_queue_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            first, second = Journal(path, queue_limit=1), Journal(path, queue_limit=1)
            barrier = threading.Barrier(2)
            outcomes = []
            def worker(journal):
                value = request()
                barrier.wait(timeout=5)
                try:
                    outcomes.append(journal.receive(42, value, decision(value), now=NOW)[1])
                except BrokerError as exc:
                    outcomes.append(exc.code)
            threads = [threading.Thread(target=worker, args=(journal,)) for journal in (first, second)]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(6)
                    self.assertFalse(thread.is_alive())
                self.assertCountEqual(outcomes, ["ready", "QUEUE_FULL"])
                self.assertEqual(first.status()["counts"], {"ready": 1})
            finally:
                first.close()
                second.close()

    def test_two_connections_reuse_one_request_without_duplicate_insert(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            first, second = Journal(path), Journal(path)
            value = request()
            barrier = threading.Barrier(2)
            outcomes = []
            def worker(journal):
                barrier.wait(timeout=5)
                outcomes.append(journal.receive(42, value, decision(value), now=NOW))
            threads = [threading.Thread(target=worker, args=(journal,)) for journal in (first, second)]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(6)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(len(outcomes), 2)
                self.assertEqual(outcomes[0], outcomes[1])
                self.assertEqual(first.status()["counts"], {"ready": 1})
            finally:
                first.close()
                second.close()

    def test_start_checks_expiry_without_an_expiry_sweeper(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "state.sqlite3")
            try:
                value = request(ttl=1)
                key, _ = journal.receive(42, value, decision(value), now=NOW)
                with self.assertRaises(BrokerError) as caught:
                    journal.start(key, possible_effect=False, now=NOW + timedelta(seconds=2))
                self.assertEqual(caught.exception.code, "EXPIRED")
                self.assertIsNone(journal.get(key)["attempt_id"])
            finally:
                journal.close()

    def test_pause_blocks_start_and_notification_without_erasing_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "state.sqlite3")
            try:
                value = request()
                key, _ = journal.receive(42, value, decision(value), now=NOW)
                journal.set_paused(True)
                with self.assertRaises(BrokerError) as caught:
                    journal.start(key, possible_effect=False, now=NOW)
                self.assertEqual(caught.exception.code, "PAUSED")
                journal.set_paused(False)
                journal.start(key, possible_effect=False, now=NOW)
                journal.save_result(key, {"artifact_ids": []}, now=NOW)
                journal.mark_published(key, "immutable:synthetic", now=NOW)
                journal.set_paused(True)
                with self.assertRaises(BrokerError):
                    journal.mark_notification_pending(key, "notice", now=NOW)
                self.assertEqual(journal.get(key)["state"], "published")
                self.assertEqual(journal.get(key)["publication_ref"], "immutable:synthetic")
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
