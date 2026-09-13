from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
import zlib
import struct

from interloc.capabilities.capture.greenshot import (
    CaptureError,
    CaptureScope,
    GreenshotCaptureProvider,
    HotkeyChord,
    WindowIdentity,
    load_greenshot_settings,
)


def chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def png(width: int = 2, height: int = 2) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\x20\x40\x60\xff" * width
    pixels = row * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


class FakeWindows:
    def __init__(self) -> None:
        self.windows = {
            100: WindowIdentity(100, 1234, 111111, "Synthetic\x00 Window"),
            200: WindowIdentity(200, 9999, 222222, "Other"),
        }
        self.foreground = 100

    def enumerate_windows(self, allowed_pids: frozenset[int]):
        return tuple(value for value in self.windows.values() if value.pid in allowed_pids)

    def identity(self, hwnd: int):
        if hwnd not in self.windows:
            raise CaptureError("WINDOW_UNAVAILABLE")
        return self.windows[hwnd]

    def foreground_hwnd(self):
        return self.foreground


class FakeInjector:
    def __init__(self, action=None) -> None:
        self.calls = []
        self.action = action

    def send(self, chord: HotkeyChord) -> None:
        self.calls.append(chord)
        if self.action:
            self.action()


class GreenshotProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.capture = root / "capture"
        self.artifacts = root / "artifacts"
        self.capture.mkdir()
        self.artifacts.mkdir()
        self.ini = root / "Greenshot.ini"
        self.scope_id = str(uuid4())
        self.windows = FakeWindows()
        self.write_ini()
        self.injector = FakeInjector()
        self.provider = GreenshotCaptureProvider(
            scope=CaptureScope(self.scope_id, (1234,)),
            greenshot_ini=self.ini,
            capture_directory=self.capture,
            artifact_directory=self.artifacts,
            windows=self.windows,
            injector=self.injector,
            enabled=True,
            poll_seconds=0.001,
            timeout_seconds=0.05,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_ini(
        self,
        *,
        destinations="FileDefault",
        interactive="false",
        output_format="png",
        clipboard="false",
        hotkey="Alt + PrintScreen",
        region="PrintScreen",
        output_path: Path | None = None,
    ) -> None:
        output_path = output_path or self.capture
        self.ini.write_text(
            "[Core]\n"
            f"Destinations={destinations}\n"
            f"CaptureWindowsInteractive={interactive}\n"
            f"OutputFileFormat={output_format}\n"
            f"OutputFileCopyPathToClipboard={clipboard}\n"
            f"OutputFilePath={output_path}\n"
            f"WindowHotkey={hotkey}\n"
            f"RegionHotkey={region}\n"
            "FullscreenHotkey=Ctrl + PrintScreen\n"
            "LastregionHotkey=Shift + PrintScreen\n",
            encoding="utf-8",
        )

    def lease(self):
        leases = self.provider.list_windows(self.scope_id)
        self.assertEqual(len(leases), 1)
        return leases[0]

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(CaptureError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_valid_config_selects_fixed_printscreen_chord(self):
        settings = load_greenshot_settings(self.ini, self.capture.resolve())
        self.assertEqual(settings.hotkey, HotkeyChord(("Alt",), "PrintScreen"))
        self.assertEqual(settings.output_directory, self.capture.resolve())

    def test_config_rejects_picker_clipboard_interactive_format_and_arbitrary_key(self):
        cases = [
            {"destinations": "Picker"},
            {"clipboard": "true"},
            {"interactive": "true"},
            {"output_format": "jpg"},
            {"hotkey": "Alt + F12"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.write_ini(**kwargs)
                self.assert_code(
                    "GREENSHOT_CONFIG_MISMATCH" if "hotkey" not in kwargs else "GREENSHOT_HOTKEY_UNSUPPORTED",
                    lambda: load_greenshot_settings(self.ini, self.capture.resolve()),
                )
        self.write_ini(hotkey="PrintScreen", region="PrintScreen")
        self.assert_code(
            "GREENSHOT_HOTKEY_UNSUPPORTED",
            lambda: load_greenshot_settings(self.ini, self.capture.resolve()),
        )

    def test_config_rejects_wrong_output_directory(self):
        other = Path(self.temp.name) / "other"
        other.mkdir()
        self.write_ini(output_path=other)
        self.assert_code(
            "GREENSHOT_CONFIG_MISMATCH",
            lambda: load_greenshot_settings(self.ini, self.capture.resolve()),
        )

    def test_provider_disabled_and_scope_denied(self):
        disabled = GreenshotCaptureProvider(
            scope=CaptureScope(self.scope_id, (1234,)),
            greenshot_ini=self.ini,
            capture_directory=self.capture,
            artifact_directory=self.artifacts,
            windows=self.windows,
            injector=self.injector,
        )
        self.assert_code("PROVIDER_DISABLED", lambda: disabled.list_windows(self.scope_id))
        self.assert_code("SCOPE_DENIED", lambda: self.provider.list_windows(str(uuid4())))

    def test_inventory_is_allowlisted_and_title_is_control_stripped(self):
        lease = self.lease()
        self.assertEqual(lease.identity.pid, 1234)
        self.assertEqual(lease.title, "Synthetic  Window")

    def test_wrong_foreground_fails_before_injection(self):
        lease = self.lease()
        self.windows.foreground = 200
        self.assert_code(
            "WINDOW_NOT_FOREGROUND",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )
        self.assertEqual(self.injector.calls, [])

    def test_changed_process_identity_fails_before_injection(self):
        lease = self.lease()
        self.windows.windows[100] = WindowIdentity(100, 1234, 333333, "Synthetic")
        self.assert_code(
            "WINDOW_IDENTITY_CHANGED",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )
        self.assertEqual(self.injector.calls, [])

    def test_expired_opaque_window_id_fails(self):
        now = [10.0]
        provider = GreenshotCaptureProvider(
            scope=CaptureScope(self.scope_id, (1234,)),
            greenshot_ini=self.ini,
            capture_directory=self.capture,
            artifact_directory=self.artifacts,
            windows=self.windows,
            injector=self.injector,
            enabled=True,
            lease_seconds=1.0,
            monotonic=lambda: now[0],
            poll_seconds=0.001,
            timeout_seconds=0.05,
        )
        lease = provider.list_windows(self.scope_id)[0]
        now[0] = 11.0
        self.assert_code(
            "WINDOW_ID_EXPIRED",
            lambda: provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )

    def test_success_hashes_and_copies_one_local_png_without_deleting_source(self):
        payload = png(3, 2)
        self.injector.action = lambda: (self.capture / "shot.png").write_bytes(payload)
        lease = self.lease()
        result = self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id)
        self.assertEqual(self.injector.calls, [HotkeyChord(("Alt",), "PrintScreen")])
        self.assertEqual((result.width, result.height), (3, 2))
        self.assertEqual(result.byte_length, len(payload))
        self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(result.delivery_state, "local_only")
        self.assertEqual(result.source, "greenshot")
        self.assertTrue((self.capture / "shot.png").is_file())
        self.assertEqual(result.local_path.read_bytes(), payload)
        self.assertEqual(result.local_path.parent, self.artifacts.resolve())

    def test_multiple_outputs_are_ambiguous(self):
        def write_two():
            (self.capture / "one.png").write_bytes(png())
            (self.capture / "two.png").write_bytes(png())

        self.injector.action = write_two
        lease = self.lease()
        self.assert_code(
            "OUTPUT_AMBIGUOUS",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )
        self.assertEqual(list(self.artifacts.iterdir()), [])

    def test_timeout_is_failure_not_desktop_fallback(self):
        lease = self.lease()
        self.assert_code(
            "OUTPUT_TIMEOUT",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )
        self.assertEqual(list(self.artifacts.iterdir()), [])

    def test_malformed_png_and_oversize_dimensions_are_rejected(self):
        lease = self.lease()
        self.injector.action = lambda: (self.capture / "bad.png").write_bytes(b"not a png" * 20)
        self.assert_code(
            "IMAGE_INVALID",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )
        (self.capture / "bad.png").unlink()

        self.injector.action = lambda: (self.capture / "huge.png").write_bytes(png(4097, 1))
        self.assert_code(
            "IMAGE_DIMENSIONS_UNSUPPORTED",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )

    def test_requester_cannot_supply_a_key_sequence(self):
        lease = self.lease()
        with self.assertRaises(TypeError):
            self.provider.capture(
                window_id=lease.window_id,
                capture_scope_id=self.scope_id,
                hotkey="Ctrl + Alt + Delete",
            )

    def test_output_symlink_is_rejected_when_supported(self):
        target = Path(self.temp.name) / "outside.png"
        target.write_bytes(png())

        def make_link():
            try:
                (self.capture / "shot.png").symlink_to(target)
            except (OSError, NotImplementedError):
                raise unittest.SkipTest("symlink creation unavailable")

        self.injector.action = make_link
        lease = self.lease()
        self.assert_code(
            "OUTPUT_UNSAFE",
            lambda: self.provider.capture(window_id=lease.window_id, capture_scope_id=self.scope_id),
        )


if __name__ == "__main__":
    unittest.main()
