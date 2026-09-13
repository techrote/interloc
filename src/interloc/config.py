"""Local-only configuration primitives for Interlocuator."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

APP_DIRNAME = "Interlocuator"


def default_local_root(env: dict[str, str] | None = None) -> Path:
    """Return the local runtime root without creating it."""

    values = os.environ if env is None else env
    override = values.get("INTERLOC_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = values.get("LOCALAPPDATA")
        if not base:
            raise RuntimeError("LOCALAPPDATA is unavailable; set INTERLOC_HOME explicitly")
        return Path(base) / APP_DIRNAME
    xdg = values.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / APP_DIRNAME


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    request_bytes: int = 32 * 1024
    text_chunk_bytes: int = 32 * 1024
    manifest_bytes: int = 64 * 1024
    pending_requests: int = 100

    def validate(self) -> None:
        values = (self.request_bytes, self.text_chunk_bytes, self.manifest_bytes, self.pending_requests)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("runtime limits must be positive integers")
        if self.request_bytes > 1024 * 1024 or self.text_chunk_bytes > 1024 * 1024:
            raise ValueError("request/chunk limits exceed the IL-001 safety ceiling")
        if self.manifest_bytes > 4 * 1024 * 1024 or self.pending_requests > 10_000:
            raise ValueError("manifest/queue limits exceed the IL-001 safety ceiling")


@dataclass(frozen=True, slots=True)
class AppConfig:
    local_root: Path
    limits: RuntimeLimits = RuntimeLimits()

    @classmethod
    def defaults(cls, env: dict[str, str] | None = None) -> "AppConfig":
        config = cls(local_root=default_local_root(env))
        config.validate()
        return config

    def validate(self) -> None:
        self.limits.validate()
        if not self.local_root.is_absolute():
            raise ValueError("local_root must be absolute")
