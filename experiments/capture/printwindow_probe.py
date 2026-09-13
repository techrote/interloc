from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

if os.name != "nt":
    raise SystemExit("printwindow_probe.py is Windows-only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_CLIENTONLY = 0x00000001
BI_RGB = 0
DIB_RGB_COLORS = 0


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL


def _try_enable_per_monitor_dpi() -> None:
    fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if fn is None:
        return
    fn.argtypes = [ctypes.c_void_p]
    fn.restype = wintypes.BOOL
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4.
    fn(ctypes.c_void_p(-4))


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def resolve_exact_title(title: str) -> int:
    matches: list[int] = []

    @WNDENUMPROC
    def callback(hwnd: int, _lparam: int) -> bool:
        if window_title(hwnd) == title:
            matches.append(int(hwnd))
        return True

    if not user32.EnumWindows(callback, 0):
        raise OSError(ctypes.get_last_error(), "EnumWindows failed")
    if len(matches) != 1:
        raise RuntimeError(f"exact title matched {len(matches)} windows; expected exactly one")
    return matches[0]


def window_metadata(hwnd: int) -> dict[str, object]:
    if not user32.IsWindow(hwnd):
        raise RuntimeError("target HWND is no longer valid")
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError(ctypes.get_last_error(), "GetWindowRect failed")
    dpi = None
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if get_dpi is not None:
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT
        dpi = int(get_dpi(hwnd))
    return {
        "hwnd": f"0x{int(hwnd):x}",
        "pid": int(pid.value),
        "tid": int(tid),
        "title": window_title(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "minimized": bool(user32.IsIconic(hwnd)),
        "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
        "dpi": dpi,
    }


def capture(hwnd: int, client_only: bool) -> tuple[bytes, int, int, float]:
    rect = RECT()
    get_rect = user32.GetClientRect if client_only else user32.GetWindowRect
    if not get_rect(hwnd, ctypes.byref(rect)):
        raise OSError(ctypes.get_last_error(), "window dimension query failed")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        raise RuntimeError(f"unsupported capture dimensions {width}x{height}")

    source_dc = user32.GetDC(hwnd)
    if not source_dc:
        raise OSError(ctypes.get_last_error(), "GetDC failed")
    memory_dc = gdi32.CreateCompatibleDC(source_dc)
    if not memory_dc:
        user32.ReleaseDC(hwnd, source_dc)
        raise OSError(ctypes.get_last_error(), "CreateCompatibleDC failed")

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down DIB
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(source_dc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not bitmap or not bits.value:
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, source_dc)
        raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")

    old = gdi32.SelectObject(memory_dc, bitmap)
    started = time.perf_counter()
    ok = bool(user32.PrintWindow(hwnd, memory_dc, PW_CLIENTONLY if client_only else 0))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
        if not ok:
            raise RuntimeError("PrintWindow returned failure")
        pixels = ctypes.string_at(bits, width * height * 4)
    finally:
        gdi32.SelectObject(memory_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, source_dc)
    return pixels, width, height, elapsed_ms


def bmp_bytes(pixels: bytes, width: int, height: int) -> bytes:
    info = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        -height,
        1,
        32,
        BI_RGB,
        len(pixels),
        0,
        0,
        0,
        0,
    )
    offset = 14 + len(info)
    header = struct.pack("<2sIHHI", b"BM", offset + len(pixels), 0, 0, offset)
    return header + info + pixels


def blank_suspect(pixels: bytes) -> bool:
    if not pixels:
        return True
    pixel_count = len(pixels) // 4
    step = max(1, pixel_count // 2048)
    samples = {pixels[i * 4 : i * 4 + 4] for i in range(0, pixel_count, step)}
    return len(samples) <= 2


def main() -> int:
    parser = argparse.ArgumentParser(description="IL-012 scoped PrintWindow comparator")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--hwnd", type=lambda value: int(value, 0))
    target.add_argument("--title-exact")
    parser.add_argument("--expect-pid", type=int)
    parser.add_argument("--client-only", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    _try_enable_per_monitor_dpi()
    hwnd = args.hwnd if args.hwnd is not None else resolve_exact_title(args.title_exact)
    before = window_metadata(hwnd)
    if args.expect_pid is not None and before["pid"] != args.expect_pid:
        raise RuntimeError(f"PID mismatch: expected {args.expect_pid}, observed {before['pid']}")

    pixels, width, height, elapsed_ms = capture(hwnd, args.client_only)
    after = window_metadata(hwnd)
    if after["pid"] != before["pid"]:
        raise RuntimeError("target PID changed during capture")

    payload = bmp_bytes(pixels, width, height)
    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)

    result = {
        "backend": "printwindow",
        "client_only": bool(args.client_only),
        "width": width,
        "height": height,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "blank_suspect": blank_suspect(pixels),
        "capture_ms": round(elapsed_ms, 3),
        "target_before": before,
        "target_after": after,
        "output_name": output.name,
    }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"backend": "printwindow", "error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        raise SystemExit(2)
