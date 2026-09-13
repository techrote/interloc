from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from interloc.cli import main
from interloc.config import AppConfig, RuntimeLimits, default_local_root
from interloc.platforms import probe_native_providers


class FoundationTests(unittest.TestCase):
    def test_help_is_available_without_credentials(self) -> None:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(main([]), 0)
        self.assertIn("Interlocuator local broker/courier", stdout.getvalue())

    def test_doctor_is_utf8_json_and_does_not_create_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "状态-Interloc"
            with mock.patch.dict(os.environ, {"INTERLOC_HOME": str(root)}, clear=False):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    self.assertEqual(main(["doctor", "--json"]), 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["local_root"], str(root))
            self.assertFalse(payload["local_root_exists"])
            self.assertFalse(root.exists())

    def test_explicit_local_root_must_be_absolute(self) -> None:
        with self.assertRaises(ValueError):
            AppConfig(local_root=Path("relative")).validate()

    def test_limits_are_bounded(self) -> None:
        RuntimeLimits().validate()
        with self.assertRaises(ValueError):
            RuntimeLimits(request_bytes=2 * 1024 * 1024).validate()
        with self.assertRaises(ValueError):
            RuntimeLimits(pending_requests=0).validate()

    def test_env_override_is_deterministic(self) -> None:
        expected = Path("/tmp/interloc-test-root")
        self.assertEqual(default_local_root({"INTERLOC_HOME": str(expected)}), expected)

    def test_native_probe_never_requires_provider(self) -> None:
        statuses = probe_native_providers()
        self.assertTrue(statuses)
        self.assertIn(statuses[0].status, {"not-applicable", "not-installed", "available"})

    def test_source_tree_runtime_names_are_ignored(self) -> None:
        ignore = (Path(__file__).parents[1] / ".gitignore").read_text(encoding="utf-8")
        for name in ("state/", "spool/", "artifacts/", "*.sqlite*"):
            self.assertIn(name, ignore)


if __name__ == "__main__":
    unittest.main()
