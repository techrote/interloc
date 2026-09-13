"""Deterministic local authority, persistence and hostile-input regression tests."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from interloc.broker import BrokerError, CapabilityRegistry, CapabilitySpec, Dispatcher, Journal
from interloc.cli import main
from interloc.cli.control import ControlError, ControlService, load_enrollment, read_local_file, validate_home
from interloc.policy import Enrollment, Grant, PolicyError, TransportOrigin
from interloc.protocol import ProtocolError

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
EPOCH = "00000000-0000-4000-8000-000000000001"


def request(*, at=NOW, ttl=300, identifier=None, label="synthetic"):
    return {"schema_version": 1, "request_id": identifier or str(uuid4()),
            "mailbox_epoch": EPOCH, "target_device": "test-device", "requester_label": label,
            "created_at": at.isoformat(), "expires_at": (at + timedelta(seconds=ttl)).isoformat(),
            "capability": "system.ping", "arguments": {"nonce": "synthetic-only"}}


def encode(value):
    return json.dumps(value).encode("utf-8")


def enrollment(decision="confirm"):
    return Enrollment(42, EPOCH, "test-device", True, "inbox", "outbox/test-device",
                      grants=(Grant("system.ping", "device", decision),))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "state.sqlite3"
        self.journal = Journal(self.path, queue_limit=3)
        self.enrollment = enrollment()
        self.now = NOW
        self.target = {"identity": "fixture-a"}
        self.service = self.new_service(self.journal)
        self.calls = []
        self.registry = CapabilityRegistry()
        self.registry.register(CapabilitySpec("system.ping", lambda r, c: self.calls.append(c.attempt_id) or {"artifact_ids": []}))

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def new_service(self, journal):
        return ControlService(journal, lambda: self.enrollment, target_state=lambda r, e: dict(self.target), clock=lambda: self.now)

    def admit(self, **kw):
        return self.service.admit(encode(request(**kw)))[0]

    def approve(self, key, **kw):
        preview = self.service.preview(key)
        return self.service.approve(key, preview=preview, typed_digest=preview.digest, **kw)

    def assert_code(self, code, callable_, *args, **kw):
        with self.assertRaises((BrokerError, ControlError, PolicyError, ProtocolError)) as caught:
            callable_(*args, **kw)
        self.assertEqual(caught.exception.code, code)

    def test_preview_binds_identity_destination_effect_and_expiry(self):
        key = self.admit()
        preview = self.service.preview(key)
        self.assertEqual(preview.destination_repository_id, 42)
        self.assertEqual(preview.destination_branch, "outbox/test-device")
        self.assertEqual(preview.mailbox_epoch, EPOCH)
        self.assertEqual(preview.target_device, "test-device")
        self.assertEqual(preview.scope, "device")
        self.assertEqual(preview.capability, "system.ping")
        self.assertEqual(preview.state, "awaiting_approval")
        self.assertEqual(len(preview.digest), 64)
        self.assertEqual(len(preview.target_state_sha256), 64)
        self.assertIn("nonce", preview.effect)
        self.assertGreater(preview.expires_at, NOW.isoformat())

    def test_import_and_transport_equivalence_with_separate_origin_record(self):
        data = encode(request())
        local, state = self.service.admit(data)
        with tempfile.TemporaryDirectory() as other:
            journal = Journal(Path(other) / "state.sqlite3")
            try:
                remote, state2 = self.new_service(journal).admit(data, origin=TransportOrigin(42, "inbox", EPOCH))
                self.assertEqual((local, state), (remote, state2))
                self.assertEqual(self.service.preview(local), self.new_service(journal).preview(remote))
                self.assertEqual(journal.authority(remote)["binding"]["source"], "transport")
            finally:
                journal.close()
        self.assertEqual(self.journal.authority(local)["binding"]["source"], "local_import")

    def test_forged_actor_label_cannot_override_wrong_origin(self):
        key, state = self.service.admit(encode(request(label="administrator-approved")), origin=TransportOrigin(999, "inbox", EPOCH))
        self.assertEqual(state, "rejected")
        self.assertEqual(self.journal.get(key)["terminal_code"], "ORIGIN_REPOSITORY_MISMATCH")
        self.assertEqual(self.calls, [])

    def test_no_grant_means_rejected_not_pending(self):
        self.enrollment = replace(self.enrollment, grants=())
        key = self.admit()
        self.assertEqual(self.journal.get(key)["state"], "rejected")
        self.assert_code("PREVIEW_STALE", self.approve, key)

    def test_full_digest_required_and_approval_itself_never_executes(self):
        key = self.admit()
        preview = self.service.preview(key)
        self.assert_code("CONFIRMATION_MISMATCH", self.service.approve, key, preview=preview, typed_digest=preview.digest[:8])
        self.assertEqual(self.approve(key), "ready")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.dispatch(key, self.registry), "result_saved")
        self.assertEqual(len(self.calls), 1)
        self.assert_code("STATE_CONFLICT", self.service.dispatch, key, self.registry)
        self.assertEqual(len(self.calls), 1)

    def test_bare_dispatcher_cannot_bypass_bound_control_requests(self):
        self.enrollment = enrollment("allow")
        key = self.admit()
        self.assert_code("AUTHORITY_REQUIRED", Dispatcher(self.journal, self.registry).execute, key, now=NOW)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.dispatch(key, self.registry), "result_saved")

    def test_changed_destination_without_revision_bump_invalidates_preview(self):
        key = self.admit()
        preview = self.service.preview(key)
        self.enrollment = replace(self.enrollment, outbox_branch="outbox/changed")
        self.assert_code("AUTHORITY_CHANGED", self.service.approve, key, preview=preview, typed_digest=preview.digest)
        self.assertEqual(self.journal.get(key)["state"], "awaiting_approval")

    def test_changed_policy_without_revision_bump_cannot_inherit_consent(self):
        key = self.admit()
        self.approve(key)
        self.enrollment = enrollment("allow")
        self.assert_code("AUTHORITY_CHANGED", self.service.dispatch, key, self.registry)
        self.assertEqual(self.calls, [])

    def test_target_change_between_preview_and_confirmation(self):
        key = self.admit()
        preview = self.service.preview(key)
        self.target["identity"] = "different-window"
        self.assert_code("TARGET_CHANGED", self.service.approve, key, preview=preview, typed_digest=preview.digest)
        self.assertIsNone(self.journal.authority(key)["approval"])

    def test_target_change_after_approval_stops_execution(self):
        key = self.admit()
        self.approve(key)
        self.target["identity"] = "fixture-b"
        self.assert_code("TARGET_CHANGED", self.service.dispatch, key, self.registry)
        self.assertEqual(self.calls, [])

    def test_expiry_during_human_prompt_never_authorizes(self):
        key = self.admit(ttl=2)
        preview = self.service.preview(key)
        self.now += timedelta(seconds=3)
        with self.assertRaises((PolicyError, BrokerError)):
            self.service.approve(key, preview=preview, typed_digest=preview.digest)
        self.assertIsNone(self.journal.authority(key)["approval"])
        self.assertEqual(self.journal.get(key)["state"], "awaiting_approval")
        self.journal.expire_due(now=self.now)
        self.assertEqual(self.journal.get(key)["state"], "expired")

    def test_approval_lifetime_is_bounded_and_persisted(self):
        key = self.admit()
        for ttl in (0, 301, True, 1.5):
            with self.subTest(ttl=ttl):
                self.assert_code("APPROVAL_LIMIT", self.approve, key, lifetime_seconds=ttl)
        self.approve(key, lifetime_seconds=1)
        self.now += timedelta(seconds=2)
        self.assert_code("AUTHORITY_CHANGED", self.service.dispatch, key, self.registry)
        self.assertEqual(self.calls, [])

    def test_request_expiry_checked_before_start_even_for_preallowed_ping(self):
        self.enrollment = enrollment("allow")
        key = self.admit(ttl=1)
        self.now += timedelta(seconds=2)
        with self.assertRaises((BrokerError, PolicyError)):
            self.service.dispatch(key, self.registry)
        self.assertEqual(self.journal.get(key)["state"], "expired")
        self.assertEqual(self.calls, [])

    def test_reopen_keeps_approval_and_local_denial_revokes_it(self):
        key = self.admit()
        self.approve(key)
        other = Journal(self.path)
        try:
            service = self.new_service(other)
            self.assertTrue(service.authorize(key).permitted)
            self.assertEqual(service.revoke(key), "rejected")
        finally:
            other.close()
        self.assertEqual(self.journal.get(key)["terminal_code"], "APPROVAL_REVOKED")
        self.assertIsNone(self.journal.authority(key)["approval"])
        self.assert_code("AUTHORITY_CHANGED", self.service.dispatch, key, self.registry)
        self.assertEqual(self.calls, [])

    def test_denial_is_durable_and_idempotent(self):
        value = request()
        key, _ = self.service.admit(encode(value))
        self.assertEqual(self.service.deny(key), "rejected")
        other = Journal(self.path)
        try:
            service = self.new_service(other)
            self.assertEqual(service.deny(key), "rejected")
            self.assertEqual(service.admit(encode(value)), (key, "rejected"))
        finally:
            other.close()

    def test_approval_save_failure_rolls_back_ready_state(self):
        key = self.admit()
        with patch.object(self.journal, "store_approval", side_effect=sqlite3.OperationalError("synthetic disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.approve(key)
        self.assertEqual(self.journal.get(key)["state"], "awaiting_approval")
        self.assertIsNone(self.journal.authority(key)["approval"])

    def test_collision_quarantine_survives_error_and_reopen(self):
        value = request()
        key, _ = self.service.admit(encode(value))
        value["arguments"] = {"nonce": "different"}
        self.assert_code("REQUEST_ID_COLLISION", self.service.admit, encode(value))
        other = Journal(self.path)
        try:
            self.assertEqual(other.get(key)["state"], "quarantined")
            self.assertEqual(other._db.execute("SELECT COUNT(*) FROM collisions").fetchone()[0], 1)
        finally:
            other.close()

    def test_pause_durable_across_connections_blocks_full_queue_and_approval(self):
        keys = [self.admit() for _ in range(3)]
        self.assert_code("QUEUE_FULL", self.admit)
        other = Journal(self.path)
        try:
            other.set_paused(True)
            self.assertTrue(self.journal.is_paused())
            self.assert_code("PAUSED", self.admit)
            self.assert_code("PAUSED", self.approve, keys[0])
            self.assertEqual(self.journal.status()["counts"], {"awaiting_approval": 3})
            self.assertTrue(self.journal.status(limit=1)["truncated"])
        finally:
            other.close()
        self.assertTrue(self.journal.is_paused())
        self.journal.set_paused(False)
        self.assertEqual(self.approve(keys[0]), "ready")

    def test_pause_does_not_wait_for_running_handler_and_blocks_publication(self):
        self.enrollment = enrollment("allow")
        key = self.admit()
        started, finish = threading.Event(), threading.Event()
        errors = []
        registry = CapabilityRegistry()
        def handler(req, ctx):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("synthetic test timeout")
            return {"artifact_ids": []}
        registry.register(CapabilitySpec("system.ping", handler))
        def worker():
            try:
                self.service.dispatch(key, registry)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            other = Journal(self.path)
            try:
                before = time.monotonic()
                other.set_paused(True)
                self.assertLess(time.monotonic() - before, 1.0)
                self.assertEqual(other.status()["running"], 1)
                self.assertFalse(finish.is_set())
            finally:
                other.close()
        finally:
            finish.set()
            thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.journal.get(key)["state"], "result_saved")
        self.assert_code("PAUSED", self.journal.mark_published, key, "test-commit:manifest", now=NOW)
        self.assert_code("TOO_LATE", self.service.revoke, key)

    def test_interrupt_after_start_is_left_for_indeterminate_recovery(self):
        self.enrollment = enrollment("allow")
        key = self.admit()
        registry = CapabilityRegistry()
        def interrupted(req, ctx):
            raise KeyboardInterrupt
        registry.register(CapabilitySpec("system.ping", interrupted))
        with self.assertRaises(KeyboardInterrupt):
            self.service.dispatch(key, registry)
        self.assertEqual(self.journal.get(key)["state"], "running")
        self.assertEqual(self.journal.recover_running(registry, now=NOW)["indeterminate"], 1)

    def test_default_provider_refuses_unavailable_capture(self):
        value = request(ttl=60)
        value["capability"] = "capture.window"
        value["arguments"] = {"capture_scope_id": str(uuid4()), "window_id": str(uuid4()), "format": "png"}
        service = ControlService(self.journal, lambda: self.enrollment, clock=lambda: NOW)
        self.assert_code("TARGET_UNAVAILABLE", service.admit, encode(value))
        self.assertEqual(self.journal.status()["counts"], {})


class TTY(io.StringIO):
    def isatty(self):
        return True


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="interloc space ")
        self.root = Path(self.temp.name)
        self.home = self.root / "local data"
        self.cfg = self.root / "enrollment.json"
        self.cfg.write_bytes(encode({"schema_version": 1, **asdict(enrollment())}))
        self.value = request(at=datetime.now(timezone.utc))
        self.input = self.root / "request.json"
        self.input.write_bytes(encode(self.value))
        self.base = ["--home", str(self.home), "--config", str(self.cfg)]

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, stdin=None):
        output = io.StringIO()
        with patch("sys.stdout", output), patch("sys.stdin", stdin if stdin is not None else io.StringIO()):
            status = main(self.base + list(args))
        return status, output.getvalue()

    def import_request(self):
        rc, output = self.run_cli("request", "import", str(self.input))
        self.assertEqual(rc, 0, output)
        return json.loads(output)

    def test_doctor_and_empty_status_never_create_runtime_state(self):
        for args in (("doctor", "--json"), ("status", "--json")):
            rc, output = self.run_cli(*args)
            self.assertEqual(rc, 0, output)
            self.assertTrue(json.loads(output)["ok"])
            self.assertFalse(self.home.exists())

    def test_scripted_stdin_cannot_automatically_approve(self):
        imported = self.import_request()
        rc, output = self.run_cli("approve", imported["request_id"], stdin=io.StringIO(imported["digest"] + "\n"))
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.splitlines()[-1])["error"]["code"], "INTERACTIVE_REQUIRED")
        rc, shown = self.run_cli("request", "show", imported["request_id"])
        self.assertEqual(json.loads(shown)["preview"]["state"], "awaiting_approval")

    def test_interactive_full_digest_flow_does_not_execute(self):
        imported = self.import_request()
        rc, output = self.run_cli("approve", imported["request_id"], stdin=TTY(imported["digest"] + "\n"))
        self.assertEqual(rc, 0, output)
        outcome = json.loads(output.splitlines()[-1])
        self.assertEqual(outcome["state"], "ready")
        self.assertFalse(outcome["executed"])
        rc, output = self.run_cli("revoke", imported["request_id"])
        self.assertEqual(json.loads(output)["state"], "rejected")

    def test_empty_confirmation_cancels_and_wrong_digest_fails(self):
        imported = self.import_request()
        rc, output = self.run_cli("approve", imported["request_id"], stdin=TTY("\n"))
        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(output.splitlines()[-1])["approved"])
        rc, output = self.run_cli("approve", imported["request_id"], stdin=TTY("0" * 64 + "\n"))
        self.assertEqual(rc, 2)
        self.assertIn("CONFIRMATION_MISMATCH", output)

    def test_pause_resume_do_not_require_enrollment_or_approve(self):
        self.cfg.unlink()
        rc, output = self.run_cli("pause")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(output)["paused"])
        rc, output = self.run_cli("status")
        self.assertTrue(json.loads(output)["paused"])
        rc, output = self.run_cli("resume")
        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(output)["paused"])
        self.assertEqual(json.loads(output)["counts"], {})

    def test_request_error_does_not_echo_secrets_or_terminal_controls(self):
        secret = "SYNTHETIC_SECRET_DO_NOT_ECHO"
        self.value[secret] = "\x1b]52;c;YXNk\x07"
        self.input.write_bytes(encode(self.value))
        rc, output = self.run_cli("request", "import", str(self.input))
        self.assertEqual(rc, 2)
        self.assertNotIn(secret, output)
        self.assertNotIn("\x1b", output)
        self.assertNotIn(str(self.input), output)
        self.assertIn("remedy", json.loads(output)["error"])

    def test_invalid_argv_is_structured_and_never_echoed(self):
        rc, output = self.run_cli("\x1b]52;c;malicious\a")
        self.assertEqual(rc, 2)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("malicious", output)
        self.assertEqual(json.loads(output)["error"]["code"], "ARGUMENT_INVALID")

    def test_config_unknown_fields_duplicate_keys_and_boolean_versions_fail(self):
        good = {"schema_version": 1, **asdict(enrollment())}
        variants = [{**good, "shell": "inert"}, {**good, "schema_version": True}, {**good, "repository_id": True}, {**good, "grants": [good["grants"][0]] * 129}]
        for value in variants:
            with self.subTest(value=value):
                self.cfg.write_bytes(encode(value))
                rc, output = self.run_cli("request", "import", str(self.input))
                self.assertEqual(rc, 2)
                self.assertFalse(self.home.exists())
        self.cfg.write_bytes(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ProtocolError):
            load_enrollment(self.cfg)

    def test_oversized_file_rejected_at_read_boundary(self):
        self.input.write_bytes(b"x" * 32769)
        with self.assertRaises(ControlError) as caught:
            read_local_file(self.input)
        self.assertEqual(caught.exception.code, "INPUT_TOO_LARGE")

    def test_state_in_worktree_refused_without_creating_it(self):
        project = self.root / "project"
        project.mkdir()
        (project / ".git").write_text("gitdir: synthetic", encoding="utf-8")
        with self.assertRaises(ControlError) as caught:
            validate_home(project / "runtime")
        self.assertEqual(caught.exception.code, "HOME_IN_WORKTREE")
        self.assertFalse((project / "runtime").exists())

    def test_linked_input_and_state_rejected(self):
        link = self.root / "linked.json"
        try:
            link.symlink_to(self.input)
        except OSError:
            self.skipTest("symlink creation unavailable without Windows developer privileges")
        with self.assertRaises(ControlError):
            read_local_file(link)
        (self.root / "linked-home").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ControlError):
            validate_home(self.root / "linked-home" / "state")


if __name__ == "__main__":
    unittest.main()
