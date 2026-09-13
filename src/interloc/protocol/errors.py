"""Stable protocol validation errors."""

from __future__ import annotations


class ProtocolError(ValueError):
    """A fail-closed protocol validation error with a stable machine code."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}: {message}")
