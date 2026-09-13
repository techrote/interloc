from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from uuid import uuid4

from interloc.collectors import CollectorError, GitSnapshot, SessionEnrollment, collect_command, sanitize_child_environment, tail_log


def enroll(cwd: Path, kind="command", *, repo="repo", wt="wt", encoding="utf-8", log_path=None):
    return SessionEnrollment(str(uuid4()), kind, repo, wt, cwd, "fixture", encoding, log_path)


class FakeInspector:
    def __init__(self, states): self.states = iter(states)
    def snapshot(self, cwd): return next(self.states)


class CollectorTests(unittest.TestCase):
    def test_environment_is_allowlist_not_wholesale(self):
        env = sanitize_child_environment({"PATH": "x", "GH_TOKEN": "secret", "OTHER": "no"})
        self.assertEqual(env, {"PATH": "x"})
        with self.assertRaises(CollectorError): sanitize_child_environment({}, extra={"GH_TOKEN": "x"})

    def test_supervised_command_captures_stdout_stderr_unicode_and_no_newline(self):
        with tempfile.TemporaryDirectory(prefix="interloc spaced é ") as tmp:
            e = enroll(Path(tmp))
            code = "import sys; sys.stdout.buffer.write('hé'.encode()); sys.stdout.flush(); sys.stderr.buffer.write('err✓'.encode()); sys.stderr.flush()"
            fake = FakeInspector([GitSnapshot(False, error="fixture"), GitSnapshot(False, error="fixture")])
            result = collect_command(e, [sys.executable, "-c", code], inspector=fake, timeout=5)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("hé", "".join(x.text or "" for x in result.events if x.event_type == "stdout"))
            self.assertIn("err✓", "".join(x.text or "" for x in result.events if x.event_type == "stderr"))
            self.assertEqual(result.events[0].event_type, "command_start")
            self.assertEqual(result.events[-1].event_type, "command_end")

    def test_invalid_encoding_is_replaced_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = enroll(Path(tmp))
            code = "import sys; sys.stdout.buffer.write(b'\\xffok'); sys.stdout.flush()"
            result = collect_command(e, [sys.executable, "-c", code], inspector=FakeInspector([GitSnapshot(False), GitSnapshot(False)]), timeout=5)
            text = "".join(x.text or "" for x in result.events if x.event_type == "stdout")
            self.assertIn("�ok", text)

    def test_child_does_not_receive_broker_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = enroll(Path(tmp))
            previous = os.environ.get("GH_TOKEN")
            os.environ["GH_TOKEN"] = "do-not-forward"
            try:
                code = "import os,sys; sys.stdout.write(os.environ.get('GH_TOKEN','missing'))"
                result = collect_command(e, [sys.executable, "-c", code], inspector=FakeInspector([GitSnapshot(False), GitSnapshot(False)]), timeout=5)
            finally:
                if previous is None: os.environ.pop("GH_TOKEN", None)
                else: os.environ["GH_TOKEN"] = previous
            text = "".join(x.text or "" for x in result.events if x.event_type == "stdout")
            self.assertEqual(text, "missing")

    def test_timeout_is_truthful_and_child_is_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_command(enroll(Path(tmp)), [sys.executable, "-c", "import time; time.sleep(10)"], inspector=FakeInspector([GitSnapshot(False), GitSnapshot(False)]), timeout=0.1)
            self.assertTrue(result.timed_out); self.assertIsNotNone(result.exit_code)
            self.assertIn("timeout", [x.event_type for x in result.events])

    def test_cancellation_is_truthful(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = threading.Event()
            threading.Timer(0.1, flag.set).start()
            result = collect_command(enroll(Path(tmp)), [sys.executable, "-c", "import time; time.sleep(10)"], inspector=FakeInspector([GitSnapshot(False), GitSnapshot(False)]), cancel_event=flag, timeout=5)
            self.assertTrue(result.cancelled); self.assertIn("cancelled", [x.event_type for x in result.events])

    def test_flood_is_chunked_and_bounded_per_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = "import sys; sys.stdout.write('x'*200000); sys.stdout.flush()"
            result = collect_command(enroll(Path(tmp)), [sys.executable, "-c", code], inspector=FakeInspector([GitSnapshot(False), GitSnapshot(False)]), timeout=10)
            chunks = [x.text for x in result.events if x.event_type == "stdout"]
            self.assertEqual(sum(len(x or "") for x in chunks), 200000)
            self.assertLessEqual(max(len(x or "") for x in chunks), 4096)

    def test_attribution_is_stable_per_session_and_change_is_explicit(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            before = GitSnapshot(True, "/a", "main", "a"*40, False)
            after = GitSnapshot(True, "/a", "feature", "b"*40, True)
            r1 = collect_command(enroll(Path(a), repo="r1", wt="w1"), [sys.executable, "-c", "print('a')"], inspector=FakeInspector([before, after]), timeout=5)
            r2 = collect_command(enroll(Path(b), repo="r2", wt="w2"), [sys.executable, "-c", "print('b')"], inspector=FakeInspector([GitSnapshot(False), GitSnapshot(False)]), timeout=5)
            self.assertTrue(all(x.repository_alias == "r1" and x.worktree_alias == "w1" for x in r1.events))
            self.assertTrue(all(x.repository_alias == "r2" and x.worktree_alias == "w2" for x in r2.events))
            self.assertTrue(r1.attribution_changed)
            self.assertIn("git-attribution-changed", r1.events[-1].detail or "")

    def test_log_tail_append_partial_utf8_and_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); log = root / "app.log"; log.write_bytes(b"first\n")
            e = enroll(root, kind="log", log_path=log)
            ev, cur = tail_log(e)
            self.assertIn("first", "".join(x.text or "" for x in ev))
            with log.open("ab") as h: h.write("é".encode()[:1])
            ev2, cur2 = tail_log(e, cur)
            self.assertEqual("".join(x.text or "" for x in ev2 if x.event_type == "stdout"), "")
            with log.open("ab") as h: h.write("é".encode()[1:] + b"done")
            ev3, cur3 = tail_log(e, cur2)
            self.assertIn("édone", "".join(x.text or "" for x in ev3 if x.event_type == "stdout"))
            log.write_bytes(b"new")
            ev4, _ = tail_log(e, cur3)
            kinds = [x.event_type for x in ev4]
            self.assertIn("rotation", kinds); self.assertIn("gap", kinds)

    def test_log_path_must_remain_under_enrolled_root(self):
        with tempfile.TemporaryDirectory() as root_s, tempfile.TemporaryDirectory() as outside_s:
            root, outside = Path(root_s), Path(outside_s); log = outside / "x.log"; log.write_text("x")
            with self.assertRaises(CollectorError) as ctx: tail_log(enroll(root, kind="log", log_path=log))
            self.assertEqual(ctx.exception.code, "PATH_ESCAPE")

    def test_log_budget_marks_more_data_without_discarding_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); log = root / "a.log"; log.write_text("x"*100)
            e = enroll(root, kind="log", log_path=log)
            ev, cur = tail_log(e, max_bytes=10)
            self.assertEqual(cur.offset, 10)
            self.assertNotIn("gap", [x.event_type for x in ev])
            ev2, cur2 = tail_log(e, cur, max_bytes=10)
            self.assertEqual(cur2.offset, 20)
            self.assertEqual(sum(len(x.text or "") for x in ev + ev2 if x.event_type == "stdout"), 20)

    def test_real_git_snapshot_when_git_available(self):
        import shutil, subprocess
        if shutil.which("git") is None: self.skipTest("git unavailable")
        from interloc.collectors import GitInspector
        with tempfile.TemporaryDirectory(prefix="git wt é ") as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "x.txt").write_text("x")
            snap = GitInspector().snapshot(root)
            self.assertTrue(snap.available); self.assertTrue(snap.dirty)


if __name__ == "__main__": unittest.main()
