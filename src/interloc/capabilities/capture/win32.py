"""Windows-only safety wrappers for the interim Greenshot bridge."""
from __future__ import annotations

import ctypes
import os

from .greenshot import CaptureError, HotkeyChord, Win32GreenshotHotkeyInjector as _RawHotkeyInjector


class Win32GreenshotHotkeyInjector(_RawHotkeyInjector):
    """Send the bounded chord only while Greenshot.exe exists in this session."""

    TH32CS_SNAPPROCESS = 0x00000002
    MAX_PATH = 260

    def __init__(self) -> None:
        super().__init__()
        if os.name != "nt":
            raise CaptureError("WINDOWS_REQUIRED")
        from ctypes import wintypes

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * self.MAX_PATH),
            ]

        self.PROCESSENTRY32W = PROCESSENTRY32W
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        self.kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self._invalid_handle = ctypes.c_void_p(-1).value
        self._wintypes = wintypes

    def _session_id(self, pid: int) -> int | None:
        value = self._wintypes.DWORD()
        if not self.kernel32.ProcessIdToSessionId(pid, ctypes.byref(value)):
            return None
        return int(value.value)

    def greenshot_running_in_session(self) -> bool:
        current_pid = int(self.kernel32.GetCurrentProcessId())
        current_session = self._session_id(current_pid)
        if current_session is None:
            raise CaptureError("GREENSHOT_PRESENCE_CHECK_FAILED")

        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        if not snapshot or int(snapshot) == self._invalid_handle:
            raise CaptureError("GREENSHOT_PRESENCE_CHECK_FAILED")
        try:
            entry = self.PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(self.PROCESSENTRY32W)
            if not self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                raise CaptureError("GREENSHOT_PRESENCE_CHECK_FAILED")
            while True:
                if str(entry.szExeFile).casefold() == "greenshot.exe":
                    if self._session_id(int(entry.th32ProcessID)) == current_session:
                        return True
                if not self.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
            return False
        finally:
            self.kernel32.CloseHandle(snapshot)

    def send(self, chord: HotkeyChord) -> None:
        if not self.greenshot_running_in_session():
            raise CaptureError("GREENSHOT_NOT_RUNNING")
        super().send(chord)
