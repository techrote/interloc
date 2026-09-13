from __future__ import annotations

import ctypes
import os
import unittest

from interloc.capabilities.capture import Win32GreenshotHotkeyInjector, Win32WindowApi


@unittest.skipUnless(os.name == "nt", "Windows-only binding smoke")
class Win32BindingTests(unittest.TestCase):
    def test_window_api_constructs_without_mutating_foreground(self):
        api = Win32WindowApi()
        foreground = api.foreground_hwnd()
        self.assertTrue(foreground is None or isinstance(foreground, int))

    def test_sendinput_structure_has_native_windows_size(self):
        injector = Win32GreenshotHotkeyInjector()
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(injector.INPUT), expected)


if __name__ == "__main__":
    unittest.main()
