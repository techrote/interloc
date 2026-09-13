"""Read-only Git status using disposable metadata, never the target's config."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Callable
from interloc.protocol.reads import ALIAS, SHA
from .process import ReadError, environment, run_bounded


@dataclass(frozen=True)
class Worktree:
    alias: str
    root: Path
    git_dir: Path
    common_dir: Path

    def validate(self) -> None:
        if not isinstance(self.alias, str) or not ALIAS.fullmatch(self.alias):
            raise ReadError("ENROLLMENT_INVALID")
        for path in (self.root, self.git_dir, self.common_dir):
            _safe(path)
            if not path.is_dir():
                raise ReadError("WORKTREE_UNAVAILABLE")
        marker = self.root / ".git"
        _safe(marker)
        if marker.is_dir():
            actual = marker
        else:
            text = _read(marker, 4096).decode("utf-8").strip()
            if not text.startswith("gitdir: "):
                raise ReadError("WORKTREE_IDENTITY_CHANGED")
            actual = _pointer(self.root, text[8:])
        if actual != self.git_dir:
            raise ReadError("WORKTREE_IDENTITY_CHANGED")
        pointer = self.git_dir / "commondir"
        _safe(pointer)
        actual_common = _pointer(self.git_dir, _read(pointer, 4096).decode("utf-8").strip()) if pointer.exists() else self.git_dir
        if actual_common != self.common_dir:
            raise ReadError("WORKTREE_IDENTITY_CHANGED")


def _safe(path: Path) -> None:
    if not path.is_absolute() or any(p in {"..", "."} for p in path.parts) or str(path).startswith(("\\\\", "//")):
        raise ReadError("PATH_DENIED")
    if any(":" in p for p in path.parts[1:]):
        raise ReadError("PATH_DENIED")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ReadError("PATH_DENIED")


def _pointer(base: Path, text: str) -> Path:
    if not text or any(ord(c) < 32 for c in text):
        raise ReadError("PATH_DENIED")
    candidate = Path(text)
    # Git's locally generated commondir uses ../..; match the normalized result
    # to an independently enrolled absolute directory, never auto-enroll it.
    candidate = Path(os.path.abspath(candidate if candidate.is_absolute() else base / candidate))
    _safe(candidate)
    return candidate


def _read(path: Path, limit: int) -> bytes:
    _safe(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ReadError("METADATA_LIMIT")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    if len(data) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ReadError("WORKTREE_CHANGED")
    return data


def _head(tree: Worktree) -> tuple[bytes, str | None, str | None]:
    raw = _read(tree.git_dir / "HEAD", 4096)
    text = raw.decode("ascii").strip()
    if SHA.fullmatch(text):
        return raw, None, text
    if not text.startswith("ref: refs/heads/"):
        raise ReadError("GIT_FORMAT_UNSUPPORTED")
    ref = text[5:]
    if not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]{1,200}", ref) or any(p in {"", ".", ".."} or p.endswith(".lock") for p in ref.split("/")):
        raise ReadError("GIT_FORMAT_UNSUPPORTED")
    value = None
    loose = tree.common_dir.joinpath(*ref.split("/"))
    _safe(loose)
    if loose.exists():
        value = _read(loose, 128).decode("ascii").strip()
    else:
        packed = tree.common_dir / "packed-refs"
        _safe(packed)
        if packed.exists():
            for line in _read(packed, 2 * 1024 * 1024).decode("ascii").splitlines():
                if line.startswith(("#", "^")) or not line:
                    continue
                fields = line.split(" ")
                if len(fields) != 2 or not SHA.fullmatch(fields[0]):
                    raise ReadError("GIT_FORMAT_UNSUPPORTED")
                if fields[1] == ref:
                    value = fields[0]
    if value is not None and not SHA.fullmatch(value):
        raise ReadError("GIT_FORMAT_UNSUPPORTED")
    return raw, ref[11:], value


def _inventory(root: Path, *, deadline: float, cancelled: Callable[[], bool], skip_git: bool) -> str:
    """Conservative bounded support envelope; links/special files are not followed."""
    pending, count, total = [root], 0, 0
    digest = hashlib.sha256()
    while pending:
        if cancelled():
            raise ReadError("CANCELLED")
        if time.monotonic() >= deadline:
            raise ReadError("PROCESS_TIMEOUT")
        current = pending.pop()
        _safe(current)
        with os.scandir(current) as entries:
            batch = []
            children = []
            for entry in entries:
                if cancelled():
                    raise ReadError("CANCELLED")
                if time.monotonic() >= deadline:
                    raise ReadError("PROCESS_TIMEOUT")
                if skip_git and current == root and entry.name == ".git":
                    continue
                count += 1
                if count > 20000:
                    raise ReadError("WORKTREE_LIMIT")
                _safe(Path(entry.path))
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                    raise ReadError("PATH_DENIED")
                total += info.st_size
                if total > 256 * 1024 * 1024:
                    raise ReadError("WORKTREE_LIMIT")
                batch.append((entry.name, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ino))
                if stat.S_ISDIR(info.st_mode):
                    children.append(Path(entry.path))
            pending.extend(sorted(children, reverse=True))
            for item in sorted(batch):
                digest.update(repr((str(current.relative_to(root)), item)).encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def inspect_worktree(tree: Worktree, executable: Path, *, cancelled: Callable[[], bool] = lambda: False,
                     runner=run_bounded) -> dict:
    """Return one conservative status observation, never run target hooks/filters.

    Conversion filters, EOL normalization, custom ignores and submodule internals
    are deliberately not inherited. Status describes this isolated configuration,
    not necessarily the user's configured git-status rendering.
    """
    try:
        tree.validate()
        deadline = time.monotonic() + 30
        before = _inventory(tree.root, deadline=deadline, cancelled=cancelled, skip_git=True)
        objects = tree.common_dir / "objects"
        _safe(objects)
        if not objects.is_dir() or (tree.common_dir / "reftable").exists():
            raise ReadError("GIT_FORMAT_UNSUPPORTED")
        for name in ("alternates", "http-alternates"):
            path = objects / "info" / name
            if path.exists() and _read(path, 4096).strip():
                raise ReadError("GIT_FORMAT_UNSUPPORTED")
        _inventory(objects, deadline=deadline, cancelled=cancelled, skip_git=False)
        raw_head, branch, head = _head(tree)
        index_path = tree.git_dir / "index"
        _safe(index_path)
        index = _read(index_path, 16 * 1024 * 1024) if index_path.exists() else None
        if index is not None and (index[:4] != b"DIRC" or len(index) < 32 or hashlib.sha1(index[:-20]).digest() != index[-20:]):
            raise ReadError("GIT_FORMAT_UNSUPPORTED")
        with tempfile.TemporaryDirectory(prefix="interloc-read-") as temp:
            root = Path(temp)
            shadow = root / "git"
            (shadow / "objects").mkdir(parents=True)
            (shadow / "refs" / "heads").mkdir(parents=True)
            (shadow / "HEAD").write_bytes(raw_head)
            if head is not None and branch is not None:
                target = shadow / "refs" / "heads" / branch
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(head + "\n", encoding="ascii")
            if index is not None:
                (shadow / "index").write_bytes(index)
            (shadow / "config").write_text("[core]\nrepositoryformatversion = 0\nbare = false\nfsmonitor = false\nuntrackedCache = false\nautocrlf = false\n", encoding="ascii")
            env = environment()
            env.update({"HOME": str(root), "XDG_CONFIG_HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
                        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                        "GIT_TERMINAL_PROMPT": "0", "GIT_OBJECT_DIRECTORY": str(objects)})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadError("PROCESS_TIMEOUT")
            completed = runner([str(executable), "--no-pager", "--no-optional-locks", "--git-dir=" + str(shadow),
                                "--work-tree=" + str(tree.root), "status", "--porcelain=v1", "-z", "--no-renames",
                                "--untracked-files=normal", "--ignore-submodules=all"], cwd=root, env=env,
                               timeout=min(15, remaining), max_stdout=262144, cancelled=cancelled)
            if completed.returncode:
                raise ReadError("GIT_STATUS_UNAVAILABLE")
        # No mutable authority is silently reused across an observation.
        tree.validate()
        if _head(tree) != (raw_head, branch, head) or (index_path.exists() != (index is not None)):
            raise ReadError("WORKTREE_CHANGED")
        if index is not None and _read(index_path, 16 * 1024 * 1024) != index:
            raise ReadError("WORKTREE_CHANGED")
        if before != _inventory(tree.root, deadline=deadline, cancelled=cancelled, skip_git=True):
            raise ReadError("WORKTREE_CHANGED")
        if completed.stdout and not completed.stdout.endswith(b"\0"):
            raise ReadError("GIT_OUTPUT_INVALID")
        rows = []
        for record in completed.stdout.split(b"\0"):
            if not record:
                continue
            if len(record) < 4 or record[2:3] != b" " or any(c not in b" MADRCU?!" for c in record[:2]):
                raise ReadError("GIT_OUTPUT_INVALID")
            path = record[3:].decode("utf-8", "strict")
            if path.startswith(("/", "\\")) or ":" in path or ".." in path.replace("\\", "/").split("/"):
                raise ReadError("GIT_OUTPUT_INVALID")
            rows.append({"status": record[:2].decode("ascii"), "path": path})
            if len(rows) > 1000:
                raise ReadError("OUTPUT_LIMIT")
        return {"worktree_alias": tree.alias, "head_sha": head, "branch": branch, "unborn": head is None,
                "dirty": bool(rows), "entries": rows, "configuration": "isolated-no-filters", "submodules": "not_inspected",
                "index_sha256": hashlib.sha256(index).hexdigest() if index is not None else None}
    except (OSError, UnicodeError) as exc:
        raise ReadError("WORKTREE_UNAVAILABLE") from exc
