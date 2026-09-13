from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from interloc.policy import Approval, Enrollment, Grant, PolicyError, TransportOrigin, evaluate, resolve_scoped_path

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
EPOCH = str(uuid4())
SESSION = str(uuid4())
SCOPE = str(uuid4())


def req(capability="system.ping", *, requester="ordinary-chat"):
    args = {"nonce": "x"}
    value = {"schema_version": 1, "request_id": str(uuid4()), "mailbox_epoch": EPOCH, "target_device": "dev-box-1", "requester_label": requester, "created_at": NOW.isoformat().replace("+00:00", "Z"), "expires_at": (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"), "capability": capability, "arguments": args}
    if capability == "terminal.tail":
        value["session_id"] = SESSION; value["arguments"] = {"max_lines": 5}
    elif capability == "window.list":
        value["arguments"] = {"capture_scope_id": SCOPE}
    elif capability == "capture.window":
        value["arguments"] = {"capture_scope_id": SCOPE, "window_id": str(uuid4()), "format": "png"}
    return value


def enrollment(*grants, revision=1):
    return Enrollment(42, EPOCH, "dev-box-1", True, "inbox", "outbox/dev-box-1", tuple(grants), revision)


def origin(**changes):
    data = {"repository_id": 42, "branch": "inbox", "mailbox_epoch": EPOCH}; data.update(changes); return TransportOrigin(**data)


class PolicyTests(unittest.TestCase):
    def test_exact_grant_allows_ping(self):
        e = enrollment(Grant("system.ping", "device", "allow"))
        d = evaluate(req(), enrollment=e, origin=origin(), target_state={"device": "ready"}, now=NOW)
        self.assertTrue(d.permitted); self.assertEqual(d.code, "ALLOWED")

    def test_requester_label_never_changes_authority(self):
        e = enrollment(Grant("system.ping", "device", "allow"))
        a = evaluate(req(requester="trusted-admin"), enrollment=e, origin=origin(repository_id=99), target_state={}, now=NOW)
        b = evaluate(req(requester="untrusted"), enrollment=e, origin=origin(repository_id=99), target_state={}, now=NOW)
        self.assertEqual((a.action, a.code), ("deny", "ORIGIN_REPOSITORY_MISMATCH")); self.assertEqual(a.code, b.code)

    def test_epoch_branch_device_and_destination_fail_closed(self):
        e = enrollment(Grant("system.ping", "device", "allow"))
        self.assertEqual(evaluate(req(), enrollment=e, origin=origin(branch="other"), target_state={}, now=NOW).code, "ORIGIN_BRANCH_MISMATCH")
        other = req(); other["target_device"] = "elsewhere"
        self.assertEqual(evaluate(other, enrollment=e, origin=origin(), target_state={}, now=NOW).code, "DEVICE_MISMATCH")
        self.assertEqual(evaluate(req(), enrollment=e, origin=origin(), target_state={}, destination="clipboard", now=NOW).code, "DESTINATION_DENIED")
        self.assertEqual(evaluate(req(), enrollment=e, origin=origin(mailbox_epoch=str(uuid4())), target_state={}, now=NOW).code, "EPOCH_MISMATCH")

    def test_no_grant_and_explicit_deny(self):
        self.assertEqual(evaluate(req(), enrollment=enrollment(), origin=origin(), target_state={}, now=NOW).code, "NO_GRANT")
        e = enrollment(Grant("system.ping", "device", "deny"))
        self.assertEqual(evaluate(req(), enrollment=e, origin=origin(), target_state={}, now=NOW).code, "LOCAL_DENY")

    def test_capture_cannot_be_configured_auto_allow(self):
        with self.assertRaises(PolicyError) as ctx:
            enrollment(Grant("capture.window", "capture:" + SCOPE, "allow")).validate()
        self.assertEqual(ctx.exception.code, "CONFIG_INVALID")

    def test_confirmation_binds_request_policy_target_and_expiry(self):
        e = enrollment(Grant("capture.window", "capture:" + SCOPE, "confirm"), revision=7)
        r = req("capture.window")
        need = evaluate(r, enrollment=e, origin=origin(), target_state={"window": "pid:123@t1"}, now=NOW)
        self.assertEqual((need.action, need.code), ("confirm", "APPROVAL_REQUIRED"))
        approval = Approval(need.request_sha256, 7, need.target_state_sha256, NOW + timedelta(seconds=30))
        self.assertTrue(evaluate(r, enrollment=e, origin=origin(), target_state={"window": "pid:123@t1"}, approval=approval, now=NOW).permitted)
        self.assertEqual(evaluate(r, enrollment=e, origin=origin(), target_state={"window": "pid:124@t2"}, approval=approval, now=NOW).code, "APPROVAL_STALE")
        e2 = enrollment(Grant("capture.window", "capture:" + SCOPE, "confirm"), revision=8)
        self.assertEqual(evaluate(r, enrollment=e2, origin=origin(), target_state={"window": "pid:123@t1"}, approval=approval, now=NOW).code, "APPROVAL_STALE")
        expired = Approval(need.request_sha256, 7, need.target_state_sha256, NOW)
        self.assertEqual(evaluate(r, enrollment=e, origin=origin(), target_state={"window": "pid:123@t1"}, approval=expired, now=NOW).code, "APPROVAL_STALE")
        revoked = Approval(need.request_sha256, 7, need.target_state_sha256, NOW + timedelta(seconds=30), True)
        self.assertEqual(evaluate(r, enrollment=e, origin=origin(), target_state={"window": "pid:123@t1"}, approval=revoked, now=NOW).code, "APPROVAL_STALE")

    def test_approval_is_bound_to_exact_request_digest(self):
        e = enrollment(Grant("window.list", "capture:" + SCOPE, "confirm"))
        r = req("window.list")
        need = evaluate(r, enrollment=e, origin=origin(), target_state={"scope": "v1"}, now=NOW)
        approval = Approval(need.request_sha256, 1, need.target_state_sha256, NOW + timedelta(seconds=30))
        r2 = dict(r); r2["request_id"] = str(uuid4())
        self.assertEqual(evaluate(r2, enrollment=e, origin=origin(), target_state={"scope": "v1"}, approval=approval, now=NOW).code, "APPROVAL_STALE")

    def test_wildcards_unknown_capabilities_and_public_mailbox_are_invalid(self):
        for grant in (Grant("system.ping", "*", "allow"), Grant("shell.exec", "device", "allow")):
            with self.assertRaises(PolicyError): enrollment(grant).validate()
        bad = Enrollment(42, EPOCH, "dev-box-1", False, "inbox", "outbox/dev-box-1")
        with self.assertRaises(PolicyError): bad.validate()

    def test_local_path_resolution_rejects_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as root_s, tempfile.TemporaryDirectory() as outside_s:
            root, outside = Path(root_s), Path(outside_s)
            (root / "logs").mkdir(); (root / "logs" / "a.txt").write_text("ok")
            self.assertEqual(resolve_scoped_path(root, "logs/a.txt"), (root / "logs" / "a.txt").resolve())
            for bad in ("../x", "/tmp/x", "C:\\x", "//server/share", "logs/file:ads"):
                with self.assertRaises(PolicyError): resolve_scoped_path(root, bad)
            try:
                (root / "escape").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(PolicyError) as ctx: resolve_scoped_path(root, "escape/secret.txt")
            self.assertEqual(ctx.exception.code, "PATH_ESCAPE")

    def test_terminal_scope_is_exact_session(self):
        e = enrollment(Grant("terminal.tail", "session:" + SESSION, "allow"))
        self.assertTrue(evaluate(req("terminal.tail"), enrollment=e, origin=origin(), target_state={"session": "open"}, now=NOW).permitted)
        other = req("terminal.tail"); other["session_id"] = str(uuid4())
        self.assertEqual(evaluate(other, enrollment=e, origin=origin(), target_state={"session": "open"}, now=NOW).code, "NO_GRANT")


if __name__ == "__main__": unittest.main()
