"""Explicitly enrolled local command and log collectors.

The collector owns no network credentials and publishes nothing. It returns bounded
structured events to the later evidence/redaction layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import codecs
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence
from uuid import UUID, uuid4

MAX_EVENT_TEXT_CHARS = 4096
MAX_QUEUE_EVENTS = 256
MAX_LOG_READ_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 300.0
SENSITIVE_ENV = {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "AZURE_DEVOPS_EXT_PAT", "OPENAI_API_KEY"}
ENV_ALLOWLIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONUTF8",
}


class CollectorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CollectorError("ENROLLMENT_INVALID", f"{field_name} must be a UUID") from exc


def _alias(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 0x20 for c in value):
        raise CollectorError("ENROLLMENT_INVALID", f"{field_name} must be a bounded printable alias")
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_child_environment(source: Mapping[str, str] | None = None, *, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a small child environment, never a wholesale credential-bearing copy."""
    source = os.environ if source is None else source
    result = {key: value for key, value in source.items() if key.upper() in ENV_ALLOWLIST and key.upper() not in SENSITIVE_ENV}
    for key, value in (extra or {}).items():
        upper = key.upper()
        if upper not in ENV_ALLOWLIST or upper in SENSITIVE_ENV:
            raise CollectorError("ENV_DENIED", f"environment key {key!r} is not allowlisted")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    available: bool
    root: str | None = None
    branch: str | None = None
    head: str | None = None
    dirty: bool | None = None
    error: str | None = None


class GitInspector:
    """Read-only best-effort Git metadata inspection with optional locks disabled."""

    def __init__(self, executable: str | None = None, timeout: float = 3.0) -> None:
        self.executable = executable or shutil.which("git") or "git"
        self.timeout = timeout

    def _run(self, cwd: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        env = sanitize_child_environment()
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            [self.executable, "-C", str(cwd), *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=self.timeout,
            env=env, shell=False, check=False,
        )

    def snapshot(self, cwd: Path) -> GitSnapshot:
        try:
            root = self._run(cwd, ["rev-parse", "--show-toplevel"])
            if root.returncode != 0:
                return GitSnapshot(False, error="not-a-git-worktree")
            head = self._run(cwd, ["rev-parse", "--verify", "HEAD"])
            branch = self._run(cwd, ["symbolic-ref", "--quiet", "--short", "HEAD"])
            status = self._run(cwd, ["status", "--porcelain=v1", "--untracked-files=normal"])
            return GitSnapshot(
                True,
                root=root.stdout.strip() or None,
                branch=branch.stdout.strip() if branch.returncode == 0 else None,
                head=head.stdout.strip() if head.returncode == 0 else None,
                dirty=bool(status.stdout) if status.returncode == 0 else None,
                error=None if status.returncode == 0 else "status-unavailable",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return GitSnapshot(False, error=type(exc).__name__)


@dataclass(frozen=True, slots=True)
class SessionEnrollment:
    session_id: str
    source_kind: str
    repository_alias: str
    worktree_alias: str
    cwd: Path
    command_label: str
    encoding: str = "utf-8"
    log_path: Path | None = None

    def validate(self) -> None:
        _uuid(self.session_id, "session_id")
        if self.source_kind not in {"command", "log"}:
            raise CollectorError("ENROLLMENT_INVALID", "source_kind must be command or log")
        _alias(self.repository_alias, "repository_alias")
        _alias(self.worktree_alias, "worktree_alias")
        _alias(self.command_label, "command_label")
        try:
            codecs.lookup(self.encoding)
        except LookupError as exc:
            raise CollectorError("ENROLLMENT_INVALID", "unknown text encoding") from exc
        if not self.cwd.is_absolute():
            raise CollectorError("ENROLLMENT_INVALID", "cwd must be absolute")
        if self.source_kind == "log":
            if self.log_path is None or not self.log_path.is_absolute():
                raise CollectorError("ENROLLMENT_INVALID", "log source requires an explicit absolute log_path")
        elif self.log_path is not None:
            raise CollectorError("ENROLLMENT_INVALID", "command source cannot carry log_path")


@dataclass(frozen=True, slots=True)
class CollectedEvent:
    event_id: str
    session_id: str
    sequence: int
    occurred_at: datetime
    event_type: str
    repository_alias: str
    worktree_alias: str
    command_label: str
    text: str | None = None
    detail: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class CollectionResult:
    enrollment: SessionEnrollment
    events: tuple[CollectedEvent, ...]
    before: GitSnapshot
    after: GitSnapshot
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    attribution_changed: bool


class _EventBuilder:
    def __init__(self, enrollment: SessionEnrollment, clock: Callable[[], datetime]) -> None:
        self.enrollment = enrollment
        self.clock = clock
        self.sequence = 0

    def make(self, event_type: str, *, text: str | None = None, detail: str | None = None, exit_code: int | None = None) -> CollectedEvent:
        event = CollectedEvent(
            str(uuid4()), self.enrollment.session_id, self.sequence, self.clock(), event_type,
            self.enrollment.repository_alias, self.enrollment.worktree_alias, self.enrollment.command_label,
            text=text, detail=detail, exit_code=exit_code,
        )
        self.sequence += 1
        return event


def _reader(stream, event_type: str, encoding: str, out: "queue.Queue[tuple[str, str | None]]") -> None:
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    try:
        while True:
            reader = getattr(stream, "read1", stream.read)
            raw = reader(4096)
            if not raw:
                break
            text = decoder.decode(raw, final=False)
            for start in range(0, len(text), MAX_EVENT_TEXT_CHARS):
                out.put((event_type, text[start:start + MAX_EVENT_TEXT_CHARS]))
        final = decoder.decode(b"", final=True)
        if final:
            for start in range(0, len(final), MAX_EVENT_TEXT_CHARS):
                out.put((event_type, final[start:start + MAX_EVENT_TEXT_CHARS]))
    except BaseException as exc:
        out.put(("reader_error", type(exc).__name__))
    finally:
        out.put((event_type + "_eof", None))


def _terminate(process: subprocess.Popen[bytes], grace: float = 1.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=grace)
        except (OSError, subprocess.TimeoutExpired):
            pass


def collect_command(
    enrollment: SessionEnrollment,
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    inspector: GitInspector | None = None,
    clock: Callable[[], datetime] = utc_now,
    env_extra: Mapping[str, str] | None = None,
) -> CollectionResult:
    """Run one explicitly enrolled child and return bounded structured events."""
    enrollment.validate()
    if enrollment.source_kind != "command":
        raise CollectorError("SOURCE_MISMATCH", "command collection requires source_kind=command")
    if not argv or not all(isinstance(x, str) and x for x in argv):
        raise CollectorError("COMMAND_INVALID", "argv must be a non-empty sequence of strings")
    if timeout <= 0 or timeout > 24 * 60 * 60:
        raise CollectorError("TIMEOUT_INVALID", "timeout must be >0 and <=24h")
    inspector = inspector or GitInspector()
    before = inspector.snapshot(enrollment.cwd)
    builder = _EventBuilder(enrollment, clock)
    events: list[CollectedEvent] = [builder.make("command_start", detail="supervised-child-start")]
    q: "queue.Queue[tuple[str, str | None]]" = queue.Queue(maxsize=MAX_QUEUE_EVENTS)
    child_env = sanitize_child_environment(extra=env_extra)
    try:
        process = subprocess.Popen(
            list(argv), cwd=enrollment.cwd, env=child_env, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        after = inspector.snapshot(enrollment.cwd)
        events.append(builder.make("command_end", detail="spawn-failed:" + type(exc).__name__, exit_code=None))
        return CollectionResult(enrollment, tuple(events), before, after, None, False, False, before != after)
    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=_reader, args=(process.stdout, "stdout", enrollment.encoding, q), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, "stderr", enrollment.encoding, q), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    eof: set[str] = set()
    timed_out = False
    cancelled = False
    while len(eof) < 2 or process.poll() is None:
        if cancel_event is not None and cancel_event.is_set() and process.poll() is None:
            cancelled = True
            events.append(builder.make("cancelled", detail="local-cancellation"))
            _terminate(process)
        if time.monotonic() >= deadline and process.poll() is None:
            timed_out = True
            events.append(builder.make("timeout", detail="command-timeout"))
            _terminate(process)
        try:
            kind, text = q.get(timeout=0.05)
        except queue.Empty:
            continue
        if kind.endswith("_eof"):
            eof.add(kind)
        elif kind == "reader_error":
            events.append(builder.make("gap", detail="stream-reader-error:" + (text or "unknown")))
        else:
            events.append(builder.make(kind, text=text or ""))
    for thread in threads:
        thread.join(timeout=1.0)
    process.stdout.close()
    process.stderr.close()
    rc = process.poll()
    after = inspector.snapshot(enrollment.cwd)
    attribution_changed = before.available and after.available and (before.root, before.branch, before.head, before.dirty) != (after.root, after.branch, after.head, after.dirty)
    events.append(builder.make("command_end", detail="supervised-child-end" + (";git-attribution-changed" if attribution_changed else ""), exit_code=rc))
    return CollectionResult(enrollment, tuple(events), before, after, rc, timed_out, cancelled, attribution_changed)


@dataclass(frozen=True, slots=True)
class LogCursor:
    file_id: tuple[int, int] | None = None
    offset: int = 0
    partial: bytes = b""


def _file_id(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (int(stat.st_dev), int(stat.st_ino))


def tail_log(
    enrollment: SessionEnrollment,
    cursor: LogCursor = LogCursor(),
    *,
    max_bytes: int = MAX_LOG_READ_BYTES,
    clock: Callable[[], datetime] = utc_now,
) -> tuple[tuple[CollectedEvent, ...], LogCursor]:
    """Read at most max_bytes newly appended bytes from an enrolled log file."""
    enrollment.validate()
    if enrollment.source_kind != "log":
        raise CollectorError("SOURCE_MISMATCH", "log collection requires source_kind=log")
    assert enrollment.log_path is not None
    path = enrollment.log_path
    if max_bytes <= 0 or max_bytes > 1024 * 1024:
        raise CollectorError("LIMIT_INVALID", "max_bytes must be 1..1MiB")
    resolved_cwd = enrollment.cwd.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(resolved_cwd)
    except ValueError as exc:
        raise CollectorError("PATH_ESCAPE", "log path escapes enrolled cwd") from exc
    identity = _file_id(resolved)
    offset = cursor.offset
    partial = cursor.partial
    builder = _EventBuilder(enrollment, clock)
    events: list[CollectedEvent] = []
    size = resolved.stat().st_size
    if cursor.file_id is not None and cursor.file_id != identity:
        events.append(builder.make("rotation", detail="file-identity-changed"))
        offset, partial = 0, b""
    elif size < offset:
        events.append(builder.make("rotation", detail="file-truncated"))
        events.append(builder.make("gap", detail="previous-offset-beyond-new-end"))
        offset, partial = 0, b""
    with resolved.open("rb", buffering=0) as handle:
        handle.seek(offset)
        raw = handle.read(max_bytes)
        new_offset = handle.tell()
    decoder = codecs.getincrementaldecoder(enrollment.encoding)(errors="replace")
    text = decoder.decode(partial + raw, final=False)
    undecoded = decoder.getstate()[0]
    if text:
        for start in range(0, len(text), MAX_EVENT_TEXT_CHARS):
            events.append(builder.make("stdout", text=text[start:start + MAX_EVENT_TEXT_CHARS]))
    return tuple(events), LogCursor(identity, new_offset, undecoded)
