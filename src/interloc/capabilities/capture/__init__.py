"""Scoped visual-capture providers."""

from .greenshot import (
    CaptureArtifact,
    CaptureError,
    CaptureScope,
    GreenshotCaptureProvider,
    GreenshotSettings,
    HotkeyChord,
    Win32GreenshotHotkeyInjector,
    Win32WindowApi,
    WindowIdentity,
    WindowLease,
    load_greenshot_settings,
)

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
