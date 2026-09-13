"""Scoped visual-capture providers."""

from .greenshot import (
    CaptureArtifact,
    CaptureError,
    CaptureScope,
    GreenshotCaptureProvider,
    GreenshotSettings,
    HotkeyChord,
    Win32WindowApi,
    WindowIdentity,
    WindowLease,
    load_greenshot_settings,
)
from .win32 import Win32GreenshotHotkeyInjector

__all__ = [
    "CaptureArtifact",
    "CaptureError",
    "CaptureScope",
    "GreenshotCaptureProvider",
    "GreenshotSettings",
    "HotkeyChord",
    "Win32GreenshotHotkeyInjector",
    "Win32WindowApi",
    "WindowIdentity",
    "WindowLease",
    "load_greenshot_settings",
]
