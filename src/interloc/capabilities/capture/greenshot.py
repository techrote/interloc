"""Greenshot-backed scoped capture provider for the interim IL-013 backend.

This component is deliberately not runtime-wired. It exposes no general input
capability and never selects, focuses, or falls back to a desktop/monitor target.
"""
from __future__ import annotations

from dataclasses import dataclass
import configparser
import ctypes
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
import time
from typing import Callable, Protocol
from uuid import UUID, uuid4
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
HARD_MAX_PNG_BYTES = 16 * 1024 * 1024
HARD_MAX_DIMENSION = 4096
HARD_MAX_WINDOWS = 64
HARD_MAX_DIRECTORY_ENTRIES = 2048
HARD_MAX_TIMEOUT = 10.0
_HOTKEY_SPLIT = re.compile(r"\s*\+\s*")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class CaptureScope:
    capture_scope_id: str
    allowed_pids: tuple[int, ...]

    def validate(self) -> None:
        try:
            UUID(self.capture_scope_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise CaptureError("SCOPE_INVALID") from exc
        if (
            not isinstance(self.allowed_pids, tuple)
            or not self.allowed_pids
            or len(self.allowed_pids) > HARD_MAX_WINDOWS
            or any(type(pid) is not int or pid <= 0 or pid >= 2**32 for pid in self.allowed_pids)
            or len(set(self.allowed_pids)) != len(self.allowed_pids)
        ):
            raise CaptureError("SCOPE_INVALID")


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    process_created: int
    title: str = ""


@dataclass(frozen=True)
class WindowLease:
    window_id: str
    capture_scope_id: str
    identity: WindowIdentity
    title: str
    expires_monotonic: float


@dataclass(frozen=True)
class HotkeyChord:
    modifiers: tuple[str, ...]
    key: str = "PrintScreen"


@dataclass(frozen=True)
class GreenshotSettings:
    hotkey: HotkeyChord
    output_directory: Path


@dataclass(frozen=True)
class CaptureArtifact:
    artifact_id: str
    media_type: str
    byte_length: int
    sha256: str
    width: int
    height: int
    local_path: Path
    delivery_state: str = "local_only"
    source: str = "greenshot"


class WindowApi(Protocol):
    def enumerate_windows(self, allowed_pids: frozenset[int]) -> tuple[WindowIdentity, ...]:
        ...

    def identity(self, hwnd: int) -> WindowIdentity:
        ...

    def foreground_hwnd(self) -> int | None:
        ...


class HotkeyInjector(Protocol):
    def send(self, chord: HotkeyChord) -> None:
        ...


def _safe_title(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return _CONTROL_CHARS.sub(" ", value).strip()[:256]


def _absolute_directory(path: Path, *, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise CaptureError(code)
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise CaptureError(code)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CaptureError(code) from exc
    if not resolved.is_dir():
        raise CaptureError(code)
    return resolved


def _parse_bool(section: configparser.SectionProxy, key: str, *, default: bool | None = None) -> bool:
    if key not in section:
        if default is None:
            raise CaptureError("GREENSHOT_CONFIG_MISMATCH", f"missing Greenshot setting: {key}")
        return default
    try:
        return section.getboolean(key)
    except ValueError as exc:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", f"invalid boolean setting: {key}") from exc


def _parse_hotkey(raw: str) -> HotkeyChord:
    if not isinstance(raw, str) or len(raw) > 128:
        raise CaptureError("GREENSHOT_HOTKEY_UNSUPPORTED")
    parts = [part.strip() for part in _HOTKEY_SPLIT.split(raw.strip()) if part.strip()]
    if not parts:
        raise CaptureError("GREENSHOT_HOTKEY_UNSUPPORTED")
    key = parts[-1].lower().replace(" ", "")
    if key not in {"printscreen", "prtsc", "snapshot"}:
        raise CaptureError("GREENSHOT_HOTKEY_UNSUPPORTED")
    aliases = {"alt": "Alt", "ctrl": "Ctrl", "control": "Ctrl", "shift": "Shift"}
    modifiers: list[str] = []
    for part in parts[:-1]:
        normalized = aliases.get(part.lower())
        if normalized is None or normalized in modifiers:
            raise CaptureError("GREENSHOT_HOTKEY_UNSUPPORTED")
        modifiers.append(normalized)
    return HotkeyChord(tuple(modifiers))


def _split_destinations(raw: str) -> set[str]:
    if not isinstance(raw, str) or len(raw) > 1024:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH")
    return {item.strip() for item in re.split(r"[,;]", raw) if item.strip()}


def load_greenshot_settings(ini_path: Path, expected_output_directory: Path) -> GreenshotSettings:
    """Validate the installed Greenshot configuration without mutating it."""
    if not isinstance(ini_path, Path) or not ini_path.is_absolute():
        raise CaptureError("GREENSHOT_CONFIG_UNAVAILABLE")
    try:
        info = ini_path.lstat()
        if stat.S_IFMT(info.st_mode) != stat.S_IFREG or info.st_size > 2 * 1024 * 1024 or ini_path.is_symlink():
            raise CaptureError("GREENSHOT_CONFIG_UNAVAILABLE")
        raw = ini_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CaptureError("GREENSHOT_CONFIG_UNAVAILABLE") from exc

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(raw)
    except configparser.Error as exc:
        raise CaptureError("GREENSHOT_CONFIG_UNAVAILABLE") from exc
    if "Core" not in parser:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "Greenshot [Core] section is missing")
    core = parser["Core"]

    if _parse_bool(core, "CaptureWindowsInteractive", default=False):
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "CaptureWindowsInteractive must be false")

    destinations_raw = core.get("Destinations", core.get("OutputDestinations", "Picker"))
    if _split_destinations(destinations_raw) != {"FileDefault"}:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "Greenshot destination must be FileDefault only")

    if core.get("OutputFileFormat", "png").strip().lower() != "png":
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "Greenshot output format must be png")
    if _parse_bool(core, "OutputFileCopyPathToClipboard", default=True):
        raise CaptureError(
            "GREENSHOT_CONFIG_MISMATCH",
            "OutputFileCopyPathToClipboard must be false for Interloc capture",
        )

    output_raw = core.get("OutputFilePath", "").strip().strip('"')
    if not output_raw:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "OutputFilePath must be configured")
    output = Path(os.path.expandvars(os.path.expanduser(output_raw)))
    if not output.is_absolute():
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "OutputFilePath must be absolute")
    try:
        output = output.resolve(strict=True)
    except OSError as exc:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "OutputFilePath does not exist") from exc
    if output != expected_output_directory:
        raise CaptureError("GREENSHOT_CONFIG_MISMATCH", "OutputFilePath does not match enrolled capture directory")

    raw_hotkey = core.get("WindowHotkey", "Alt + PrintScreen")
    for other in ("RegionHotkey", "FullscreenHotkey", "LastregionHotkey"):
        if other in core and core.get(other, "").strip().casefold() == raw_hotkey.strip().casefold():
            raise CaptureError("GREENSHOT_HOTKEY_UNSUPPORTED", f"WindowHotkey conflicts with {other}")
    return GreenshotSettings(hotkey=_parse_hotkey(raw_hotkey), output_directory=output)


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 45 or not payload.startswith(PNG_SIGNATURE):
        raise CaptureError("IMAGE_INVALID")
    offset = len(PNG_SIGNATURE)
    width = height = None
    saw_idat = False
    saw_iend = False
    chunk_count = 0
    while offset + 12 <= len(payload):
        chunk_count += 1
        if chunk_count > 4096:
            raise CaptureError("IMAGE_INVALID")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        if length > HARD_MAX_PNG_BYTES or offset + 12 + length > len(payload):
            raise CaptureError("IMAGE_INVALID")
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        expected_crc = struct.unpack(">I", payload[data_end : data_end + 4])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(payload[data_start:data_end], actual_crc) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise CaptureError("IMAGE_INVALID")
        if chunk_count == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise CaptureError("IMAGE_INVALID")
            width, height = struct.unpack(">II", payload[data_start : data_start + 8])
            if width < 1 or height < 1 or width > HARD_MAX_DIMENSION or height > HARD_MAX_DIMENSION:
                raise CaptureError("IMAGE_DIMENSIONS_UNSUPPORTED")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or data_end + 4 != len(payload):
                raise CaptureError("IMAGE_INVALID")
            saw_iend = True
            break
        offset = data_end + 4
    if not saw_iend or not saw_idat or width is None or height is None:
        raise CaptureError("IMAGE_INVALID")
    return width, height


def _signature(path: Path) -> tuple[int, int, int]:
    info = path.lstat()
    return stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns


class GreenshotCaptureProvider:
    """Scoped local provider. No broker registration or remote publication."""

    def __init__(
        self,
        *,
        scope: CaptureScope,
        greenshot_ini: Path,
        capture_directory: Path,
        artifact_directory: Path,
        windows: WindowApi,
        injector: HotkeyInjector,
        enabled: bool = False,
        lease_seconds: float = 120.0,
        timeout_seconds: float = 10.0,
        poll_seconds: float = 0.05,
        max_png_bytes: int = HARD_MAX_PNG_BYTES,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        scope.validate()
        if type(enabled) is not bool:
            raise CaptureError("PROVIDER_CONFIG_INVALID")
        if not (0 < lease_seconds <= 120.0) or not (0 < timeout_seconds <= HARD_MAX_TIMEOUT):
            raise CaptureError("PROVIDER_CONFIG_INVALID")
        if not (0 < poll_seconds <= 0.5):
            raise CaptureError("PROVIDER_CONFIG_INVALID")
        if type(max_png_bytes) is not int or not (128 <= max_png_bytes <= HARD_MAX_PNG_BYTES):
            raise CaptureError("PROVIDER_CONFIG_INVALID")

        self.scope = scope
        self.greenshot_ini = greenshot_ini
        self.capture_directory = _absolute_directory(capture_directory, code="CAPTURE_DIRECTORY_INVALID")
        self.artifact_directory = _absolute_directory(artifact_directory, code="ARTIFACT_DIRECTORY_INVALID")
        if self.capture_directory == self.artifact_directory:
            raise CaptureError("PROVIDER_CONFIG_INVALID", "capture and artifact directories must differ")
        self.windows = windows
        self.injector = injector
        self.enabled = enabled
        self.lease_seconds = lease_seconds
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.max_png_bytes = max_png_bytes
        self.monotonic = monotonic
        self.sleeper = sleeper
        self._leases: dict[str, WindowLease] = {}

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise CaptureError("PROVIDER_DISABLED")

    def list_windows(self, capture_scope_id: str) -> tuple[WindowLease, ...]:
        self._require_enabled()
        if capture_scope_id != self.scope.capture_scope_id:
            raise CaptureError("SCOPE_DENIED")
        now = self.monotonic()
        identities = self.windows.enumerate_windows(frozenset(self.scope.allowed_pids))
        if len(identities) > HARD_MAX_WINDOWS:
            identities = identities[:HARD_MAX_WINDOWS]
        leases: list[WindowLease] = []
        for identity in identities:
            if identity.pid not in self.scope.allowed_pids:
                continue
            lease = WindowLease(
                window_id=str(uuid4()),
                capture_scope_id=self.scope.capture_scope_id,
                identity=identity,
                title=_safe_title(identity.title),
                expires_monotonic=now + self.lease_seconds,
            )
            self._leases[lease.window_id] = lease
            leases.append(lease)
        self._leases = {key: lease for key, lease in self._leases.items() if lease.expires_monotonic > now}
        return tuple(leases)

    def _lease(self, window_id: str, capture_scope_id: str) -> WindowLease:
        if capture_scope_id != self.scope.capture_scope_id:
            raise CaptureError("SCOPE_DENIED")
        try:
            UUID(window_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise CaptureError("WINDOW_ID_INVALID") from exc
        lease = self._leases.get(window_id)
        if lease is None or lease.capture_scope_id != capture_scope_id:
            raise CaptureError("WINDOW_NOT_ENROLLED")
        if self.monotonic() >= lease.expires_monotonic:
            self._leases.pop(window_id, None)
            raise CaptureError("WINDOW_ID_EXPIRED")
        return lease

    def _revalidate(self, lease: WindowLease, *, require_foreground: bool) -> WindowIdentity:
        current = self.windows.identity(lease.identity.hwnd)
        if (
            current.hwnd != lease.identity.hwnd
            or current.pid != lease.identity.pid
            or current.process_created != lease.identity.process_created
            or current.pid not in self.scope.allowed_pids
        ):
            raise CaptureError("WINDOW_IDENTITY_CHANGED")
        if require_foreground and self.windows.foreground_hwnd() != current.hwnd:
            raise CaptureError("WINDOW_NOT_FOREGROUND")
        return current

    def _snapshot(self) -> dict[str, tuple[int, int, int]]:
        result: dict[str, tuple[int, int, int]] = {}
        try:
            for index, path in enumerate(self.capture_directory.iterdir(), start=1):
                if index > HARD_MAX_DIRECTORY_ENTRIES:
                    raise CaptureError("CAPTURE_DIRECTORY_BUSY")
                if path.name.lower().endswith(".png"):
                    result[path.name] = _signature(path)
        except CaptureError:
            raise
        except OSError as exc:
            raise CaptureError("CAPTURE_DIRECTORY_UNAVAILABLE") from exc
        return result

    def _wait_for_one_output(self, baseline: dict[str, tuple[int, int, int]], deadline: float) -> Path:
        stable: tuple[str, tuple[int, int, int]] | None = None
        while self.monotonic() < deadline:
            current = self._snapshot()
            changed = [(name, sig) for name, sig in current.items() if baseline.get(name) != sig]
            if len(changed) > 1:
                raise CaptureError("OUTPUT_AMBIGUOUS")
            if len(changed) == 1:
                name, sig = changed[0]
                path = self.capture_directory / name
                mode, size, _mtime = sig
                if mode != stat.S_IFREG or path.is_symlink() or (
                    hasattr(path, "is_junction") and path.is_junction()
                ):
                    raise CaptureError("OUTPUT_UNSAFE")
                if size > self.max_png_bytes:
                    raise CaptureError("IMAGE_TOO_LARGE")
                marker = (name, sig)
                if stable == marker and size > 0:
                    return path
                stable = marker
            else:
                stable = None
            self.sleeper(self.poll_seconds)
        raise CaptureError("OUTPUT_TIMEOUT")

    def _read_stable(self, path: Path) -> bytes:
        before = _signature(path)
        if before[0] != stat.S_IFREG or before[1] <= 0:
            raise CaptureError("IMAGE_INVALID")
        if before[1] > self.max_png_bytes:
            raise CaptureError("IMAGE_TOO_LARGE")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise CaptureError("OUTPUT_UNAVAILABLE") from exc
        if before != _signature(path) or len(payload) != before[1]:
            raise CaptureError("OUTPUT_CHANGED")
        return payload

    def _store_artifact(self, payload: bytes) -> tuple[str, Path]:
        artifact_id = str(uuid4())
        final = self.artifact_directory / f"{artifact_id}.png"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".interloc-capture-",
                suffix=".tmp",
                dir=self.artifact_directory,
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, final)
            temp_path = None
        except OSError as exc:
            raise CaptureError("ARTIFACT_WRITE_FAILED") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return artifact_id, final

    def capture(self, *, window_id: str, capture_scope_id: str, format: str = "png") -> CaptureArtifact:
        self._require_enabled()
        if format != "png":
            raise CaptureError("FORMAT_DENIED")
        lease = self._lease(window_id, capture_scope_id)
        self._revalidate(lease, require_foreground=True)
        settings = load_greenshot_settings(self.greenshot_ini, self.capture_directory)
        baseline = self._snapshot()
        deadline = self.monotonic() + self.timeout_seconds
        self.injector.send(settings.hotkey)

        output = self._wait_for_one_output(baseline, deadline)
        self._revalidate(lease, require_foreground=True)
        payload = self._read_stable(output)
        width, height = _png_dimensions(payload)
        self._revalidate(lease, require_foreground=True)
        artifact_id, final = self._store_artifact(payload)
        return CaptureArtifact(
            artifact_id=artifact_id,
            media_type="image/png",
            byte_length=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            width=width,
            height=height,
            local_path=final,
        )


class Win32WindowApi:
    """Minimal HWND inventory/identity layer; never activates a window."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        if os.name != "nt":
            raise CaptureError("WINDOWS_REQUIRED")
        from ctypes import wintypes

        self.w = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int

        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def _title(self, hwnd: int) -> str:
        length = max(0, int(self.user32.GetWindowTextLengthW(hwnd)))
        buffer = ctypes.create_unicode_buffer(min(length + 1, 1025))
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return _safe_title(buffer.value)

    def _pid(self, hwnd: int) -> int:
        pid = self.w.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            raise CaptureError("WINDOW_UNAVAILABLE")
        return int(pid.value)

    def _created(self, pid: int) -> int:
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise CaptureError("WINDOW_INACCESSIBLE")
        try:
            creation = self.w.FILETIME()
            exit_time = self.w.FILETIME()
            kernel = self.w.FILETIME()
            user = self.w.FILETIME()
            if not self.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                raise CaptureError("WINDOW_INACCESSIBLE")
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            self.kernel32.CloseHandle(handle)

    def identity(self, hwnd: int) -> WindowIdentity:
        if type(hwnd) is not int or hwnd <= 0 or not self.user32.IsWindow(hwnd):
            raise CaptureError("WINDOW_UNAVAILABLE")
        if not self.user32.IsWindowVisible(hwnd):
            raise CaptureError("WINDOW_UNAVAILABLE")
        pid = self._pid(hwnd)
        return WindowIdentity(hwnd=hwnd, pid=pid, process_created=self._created(pid), title=self._title(hwnd))

    def enumerate_windows(self, allowed_pids: frozenset[int]) -> tuple[WindowIdentity, ...]:
        identities: list[WindowIdentity] = []
        callback_type = ctypes.WINFUNCTYPE(self.w.BOOL, self.w.HWND, self.w.LPARAM)

        @callback_type
        def callback(hwnd, _lparam):
            if len(identities) >= HARD_MAX_WINDOWS:
                return True
            try:
                if self.user32.IsWindowVisible(hwnd):
                    pid = self._pid(int(hwnd))
                    if pid in allowed_pids:
                        identities.append(self.identity(int(hwnd)))
            except CaptureError:
                pass
            return True

        self.user32.EnumWindows.argtypes = [callback_type, self.w.LPARAM]
        self.user32.EnumWindows.restype = self.w.BOOL
        if not self.user32.EnumWindows(callback, 0):
            raise CaptureError("WINDOW_ENUMERATION_FAILED")
        return tuple(identities)

    def foreground_hwnd(self) -> int | None:
        hwnd = self.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None


class Win32GreenshotHotkeyInjector:
    """Emit only a validated PrintScreen-based Greenshot window hotkey."""

    _VK = {"Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10, "PrintScreen": 0x2C}
    _KEYEVENTF_KEYUP = 0x0002
    _INPUT_KEYBOARD = 1

    def __init__(self) -> None:
        if os.name != "nt":
            raise CaptureError("WINDOWS_REQUIRED")
        from ctypes import wintypes

        self.w = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]

        self.INPUT = INPUT
        self.KEYBDINPUT = KEYBDINPUT
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT

    def _event(self, key: str, up: bool):
        return self.INPUT(
            type=self._INPUT_KEYBOARD,
            ki=self.KEYBDINPUT(
                wVk=self._VK[key],
                wScan=0,
                dwFlags=self._KEYEVENTF_KEYUP if up else 0,
                time=0,
                dwExtraInfo=0,
            ),
        )

    def send(self, chord: HotkeyChord) -> None:
        if chord.key != "PrintScreen" or any(modifier not in {"Ctrl", "Alt", "Shift"} for modifier in chord.modifiers):
            raise CaptureError("GREENSHOT_HOTKEY_UNSUPPORTED")
        sequence = [self._event(modifier, False) for modifier in chord.modifiers]
        sequence.extend((self._event("PrintScreen", False), self._event("PrintScreen", True)))
        sequence.extend(self._event(modifier, True) for modifier in reversed(chord.modifiers))
        array = (self.INPUT * len(sequence))(*sequence)
        sent = int(self.user32.SendInput(len(sequence), array, ctypes.sizeof(self.INPUT)))
        if sent != len(sequence):
            releases = [self._event("PrintScreen", True)]
            releases.extend(self._event(modifier, True) for modifier in reversed(chord.modifiers))
            release_array = (self.INPUT * len(releases))(*releases)
            self.user32.SendInput(len(releases), release_array, ctypes.sizeof(self.INPUT))
            raise CaptureError("HOTKEY_INJECTION_FAILED")
