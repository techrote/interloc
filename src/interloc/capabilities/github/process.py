"""Bounded pipe capture for trusted local executables, never a remote command API."""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence


class ReadError(RuntimeError):
    """Only stable codes cross the adapter boundary; native stderr stays private."""
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def environment(*, broker: bool = False) -> dict[str, str]:
    keys = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    if broker:
        keys |= {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"}
    return {k: v for k, v in os.environ.items() if k.upper() in keys}


class _WindowsJob:
    """Attach before first instruction by starting suspended, then resume its thread."""
    def __init__(self) -> None:
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        k = c.WinDLL("kernel32", use_last_error=True)
        self.k = k
        class Basic(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(c.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                        ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
        k.CreateJobObjectW.argtypes, k.CreateJobObjectW.restype = [c.c_void_p, w.LPCWSTR], w.HANDLE
        k.SetInformationJobObject.argtypes, k.SetInformationJobObject.restype = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL
        k.AssignProcessToJobObject.argtypes, k.AssignProcessToJobObject.restype = [w.HANDLE, w.HANDLE], w.BOOL
        k.TerminateJobObject.argtypes, k.TerminateJobObject.restype = [w.HANDLE, w.UINT], w.BOOL
        k.CloseHandle.argtypes, k.CloseHandle.restype = [w.HANDLE], w.BOOL
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ReadError("CONTAINMENT_UNAVAILABLE")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            self.close()
            raise ReadError("CONTAINMENT_UNAVAILABLE")

    def attach_resume(self, proc: subprocess.Popen) -> None:
        c, w, k = self.c, self.w, self.k
        if not k.AssignProcessToJobObject(self.handle, w.HANDLE(int(proc._handle))):
            raise ReadError("CONTAINMENT_UNAVAILABLE")
        class ThreadEntry(c.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]
        k.CreateToolhelp32Snapshot.argtypes, k.CreateToolhelp32Snapshot.restype = [w.DWORD, w.DWORD], w.HANDLE
        k.Thread32First.argtypes, k.Thread32First.restype = [w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL
        k.Thread32Next.argtypes, k.Thread32Next.restype = [w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL
        k.OpenThread.argtypes, k.OpenThread.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        k.ResumeThread.argtypes, k.ResumeThread.restype = [w.HANDLE], w.DWORD
        snapshot = k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == w.HANDLE(-1).value:
            raise ReadError("CONTAINMENT_UNAVAILABLE")
        try:
            entry = ThreadEntry(); entry.dwSize = c.sizeof(entry)
            found = k.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.th32OwnerProcessID == proc.pid:
                    thread = k.OpenThread(2, False, entry.th32ThreadID)
                    if not thread:
                        break
                    try:
                        if k.ResumeThread(thread) != 0xffffffff:
                            return
                    finally:
                        k.CloseHandle(thread)
                    break
                entry.dwSize = c.sizeof(entry)
                found = k.Thread32Next(snapshot, c.byref(entry))
        finally:
            k.CloseHandle(snapshot)
        raise ReadError("CONTAINMENT_UNAVAILABLE")

    def close(self) -> None:
        if self.handle:
            self.k.TerminateJobObject(self.handle, 1)
            self.k.CloseHandle(self.handle)
            self.handle = None


def run_bounded(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float = 15,
                max_stdout: int = 1024 * 1024, max_stderr: int = 16384,
                cancelled: Callable[[], bool] = lambda: False) -> ProcessResult:
    if not argv or not Path(argv[0]).is_absolute() or Path(argv[0]).suffix.lower() in {".bat", ".cmd"}:
        raise ReadError("EXECUTABLE_INVALID")
    if not 0 < timeout <= 30 or type(max_stdout) is not int or not 1 <= max_stdout <= 2 * 1024 * 1024 or not 1 <= max_stderr <= 65536:
        raise ReadError("LIMIT_INVALID")
    if cancelled():
        raise ReadError("CANCELLED")
    deadline = time.monotonic() + timeout
    proc, job = None, None
    threads = []
    stop = threading.Event()
    messages: queue.Queue = queue.Queue(maxsize=4)
    def send(message):
        while not stop.is_set():
            try:
                messages.put(message, timeout=0.05)
                return
            except queue.Full:
                pass
    def drain(pipe, stream):
        try:
            while not stop.is_set():
                block = os.read(pipe.fileno(), 4096)
                if not block:
                    break
                send((stream, block))
        except OSError:
            send((stream, None))
        finally:
            send((stream, b""))
    try:
        if os.name == "nt":
            job = _WindowsJob()
        try:
            proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=cwd, env=dict(env), shell=False, close_fds=True,
                                    start_new_session=os.name != "nt", creationflags=4 if os.name == "nt" else 0)
        except OSError as exc:
            raise ReadError("EXECUTABLE_UNAVAILABLE") from exc
        if job:
            job.attach_resume(proc)
        for stream, pipe in enumerate((proc.stdout, proc.stderr)):
            thread = threading.Thread(target=drain, args=(pipe, stream), daemon=True)
            thread.start(); threads.append(thread)
        buffers = [bytearray(), bytearray()]
        ends = set()
        while len(ends) < 2 or proc.poll() is None:
            if cancelled():
                raise ReadError("CANCELLED")
            if time.monotonic() >= deadline:
                raise ReadError("PROCESS_TIMEOUT")
            try:
                stream, block = messages.get(timeout=0.02)
            except queue.Empty:
                continue
            if block is None:
                raise ReadError("PIPE_FAILED")
            if not block:
                ends.add(stream)
                continue
            limit = max_stdout if stream == 0 else max_stderr
            if len(buffers[stream]) + len(block) > limit:
                raise ReadError("OUTPUT_LIMIT")
            buffers[stream].extend(block)
        return ProcessResult(proc.returncode, bytes(buffers[0]), bytes(buffers[1]))
    finally:
        stop.set()
        if job:
            job.close()
        if proc:
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=2)
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()
