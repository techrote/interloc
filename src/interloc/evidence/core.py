"""Sanitize, bound, hash and describe evidence before remote publication."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
from typing import Iterable, Sequence
from uuid import UUID, uuid4

MAX_SECRET_COUNT = 64
MAX_SECRET_LENGTH = 4096
DEFAULT_CHUNK_BYTES = 32 * 1024
DEFAULT_GROUP_BYTES = 256 * 1024


class EvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _literal_variants(value: str) -> set[str]:
    variants = {value}
    if "\\" in value:
        variants.add(value.replace("\\", "/"))
    if "/" in value:
        variants.add(value.replace("/", "\\"))
    return variants


class _LiteralRedactor:
    """Streaming exact-literal redaction that never emits a possible secret prefix."""
    def __init__(self, literals: Sequence[str], replacement: str = "[REDACTED]") -> None:
        items = sorted(set(literals), key=lambda x: (-len(x), x))
        if len(items) > MAX_SECRET_COUNT:
            raise EvidenceError("TOO_MANY_SECRETS", f"at most {MAX_SECRET_COUNT} literal secrets are supported")
        if any(not item or len(item) > MAX_SECRET_LENGTH for item in items):
            raise EvidenceError("INVALID_SECRET", f"secret literals must be 1..{MAX_SECRET_LENGTH} characters")
        self.items = tuple(items)
        self.prefixes = {item[:n] for item in items for n in range(1, len(item) + 1)}
        self.full = set(items)
        self.replacement = replacement
        self.pending = ""

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            self.pending += char
            while self.pending:
                if self.pending in self.full:
                    out.append(self.replacement)
                    self.pending = ""
                    break
                if self.pending in self.prefixes:
                    break
                out.append(self.pending[0])
                self.pending = self.pending[1:]
        return "".join(out)

    def finish(self) -> str:
        value = self.pending
        self.pending = ""
        return value


class _EscapeStripper:
    """Remove ANSI/ECMA-48 CSI and OSC sequences, including chunk-split ST/BEL."""
    def __init__(self) -> None:
        self.state = "normal"

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            code = ord(char)
            if self.state == "normal":
                if char == "\x1b":
                    self.state = "esc"
                elif char == "\u009b":
                    self.state = "csi"
                elif char == "\u009d":
                    self.state = "osc"
                elif code < 0x20 and char not in {"\n", "\r", "\t"}:
                    continue
                elif code == 0x7F or 0x80 <= code <= 0x9F:
                    continue
                else:
                    out.append(char)
            elif self.state == "esc":
                if char == "[":
                    self.state = "csi"
                elif char == "]":
                    self.state = "osc"
                else:
                    self.state = "normal"
            elif self.state == "csi":
                if 0x40 <= code <= 0x7E:
                    self.state = "normal"
            elif self.state == "osc":
                if char == "\x07":
                    self.state = "normal"
                elif char == "\x1b":
                    self.state = "osc_esc"
            elif self.state == "osc_esc":
                if char == "\\":
                    self.state = "normal"
                elif char == "\x1b":
                    self.state = "osc_esc"
                else:
                    self.state = "osc"
        return "".join(out)

    def finish(self) -> str:
        self.state = "normal"
        return ""


class StreamingSanitizer:
    """Stateful control stripping + exact configured secret/path redaction."""
    def __init__(self, *, secrets: Sequence[str] = (), personal_paths: Sequence[str] = (), replacement: str = "[REDACTED]") -> None:
        literals: set[str] = set(secrets)
        for path in personal_paths:
            literals.update(_literal_variants(path))
        self.stripper = _EscapeStripper()
        self.redactor = _LiteralRedactor(tuple(literals), replacement)

    def feed(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("evidence fragments must be str")
        return self.redactor.feed(self.stripper.feed(text))

    def finish(self) -> str:
        return self.redactor.feed(self.stripper.finish()) + self.redactor.finish()


@dataclass(frozen=True, slots=True)
class TextChunk:
    artifact_id: str
    sequence: int
    text: str
    byte_length: int
    sha256: str

    def descriptor(self, path: str) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "media_type": "text/plain; charset=utf-8",
            "byte_length": self.byte_length,
            "sha256": self.sha256,
            "redaction_status": "sanitized",
            "delivery_state": "local_only",
            "mailbox_path": path,
        }


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    group_id: str
    chunks: tuple[TextChunk, ...]
    truncated: bool
    dropped_sanitized_bytes: int
    sanitized_bytes: int


def _append_utf8_chunks(text: str, chunks: list[bytearray], *, max_chunk_bytes: int, remaining_group_bytes: int) -> tuple[int, int]:
    accepted = 0
    dropped = 0
    full = False
    for char in text:
        encoded = char.encode("utf-8")
        if full or len(encoded) > remaining_group_bytes:
            dropped += len(encoded)
            full = True
            continue
        if not chunks or len(chunks[-1]) + len(encoded) > max_chunk_bytes:
            chunks.append(bytearray())
        chunks[-1].extend(encoded)
        remaining_group_bytes -= len(encoded)
        accepted += len(encoded)
    return accepted, dropped


def build_text_group(
    fragments: Iterable[str], *, secrets: Sequence[str] = (), personal_paths: Sequence[str] = (),
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES, max_group_bytes: int = DEFAULT_GROUP_BYTES,
) -> EvidenceGroup:
    if not (1 <= max_chunk_bytes <= DEFAULT_CHUNK_BYTES):
        raise EvidenceError("INVALID_LIMIT", f"max_chunk_bytes must be 1..{DEFAULT_CHUNK_BYTES}")
    if not (1 <= max_group_bytes <= DEFAULT_GROUP_BYTES):
        raise EvidenceError("INVALID_LIMIT", f"max_group_bytes must be 1..{DEFAULT_GROUP_BYTES}")
    sanitizer = StreamingSanitizer(secrets=secrets, personal_paths=personal_paths)
    buffers: list[bytearray] = []
    accepted = 0
    dropped = 0

    def consume(text: str) -> None:
        nonlocal accepted, dropped
        if dropped > 0:
            dropped += len(text.encode("utf-8"))
            return
        remaining = max_group_bytes - accepted
        if remaining <= 0:
            dropped += len(text.encode("utf-8"))
            return
        a, d = _append_utf8_chunks(text, buffers, max_chunk_bytes=max_chunk_bytes, remaining_group_bytes=remaining)
        accepted += a
        dropped += d

    for fragment in fragments:
        consume(sanitizer.feed(fragment))
    consume(sanitizer.finish())
    chunks = tuple(
        TextChunk(str(uuid4()), i, data.decode("utf-8"), len(data), sha256(bytes(data)))
        for i, data in enumerate(buffers) if data
    )
    return EvidenceGroup(str(uuid4()), chunks, dropped > 0, dropped, accepted)


def text_group_export(group: EvidenceGroup, session_id: str) -> tuple[dict[str, bytes], dict[str, object]]:
    try:
        session = str(UUID(session_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise EvidenceError("INVALID_SESSION", "session_id must be a UUID") from exc
    files: dict[str, bytes] = {}
    entries: list[dict[str, object]] = []
    for chunk in group.chunks:
        path = f"sessions/{session}/chunks/{chunk.artifact_id}.txt"
        data = chunk.text.encode("utf-8")
        files[path] = data
        entries.append({"kind": "text_chunk", "path": path, "sha256": chunk.sha256, "byte_length": chunk.byte_length, "sequence": chunk.sequence})
    manifest = {
        "schema_version": 1,
        "group_id": group.group_id,
        "session_id": session,
        "truncated": group.truncated,
        "dropped_sanitized_bytes": group.dropped_sanitized_bytes,
        "sanitized_bytes": group.sanitized_bytes,
        "entries": entries,
    }
    return files, manifest


def local_image_descriptor(payload: bytes, *, media_type: str = "image/png", local_artifact_id: str | None = None) -> dict[str, object]:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if len(payload) > 16 * 1024 * 1024:
        raise EvidenceError("IMAGE_TOO_LARGE", "local image exceeds 16 MiB")
    local_id = local_artifact_id or str(uuid4())
    try:
        local_id = str(UUID(local_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise EvidenceError("INVALID_ARTIFACT_ID", "local_artifact_id must be UUID") from exc
    if media_type != "image/png":
        raise EvidenceError("UNSUPPORTED_MEDIA", "v1 visual evidence is PNG only")
    return {
        "artifact_id": str(uuid4()),
        "media_type": media_type,
        "byte_length": len(payload),
        "sha256": sha256(payload),
        "redaction_status": "review_required",
        "delivery_state": "local_only",
        "local_artifact_id": local_id,
    }


def safe_generated_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.startswith("/") and ".." not in parts and ":" not in path and "\\" not in path
