"""Strict bounded JSON and canonical serialization helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import ProtocolError

MAX_JSON_DEPTH = 32


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("DUPLICATE_KEY", f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProtocolError("INVALID_NUMBER", f"non-finite JSON number {value!r} is forbidden")


def _depth(value: Any, current: int = 0) -> int:
    if current > MAX_JSON_DEPTH:
        return current
    if isinstance(value, dict):
        return max([current] + [_depth(v, current + 1) for v in value.values()])
    if isinstance(value, list):
        return max([current] + [_depth(v, current + 1) for v in value])
    return current


def strict_loads(data: bytes | str, *, max_bytes: int = 32 * 1024) -> Any:
    if isinstance(data, str):
        raw = data.encode("utf-8")
        text = data
    elif isinstance(data, bytes):
        raw = data
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("INVALID_UTF8", "input is not valid UTF-8") from exc
    else:
        raise TypeError("data must be bytes or str")
    if len(raw) > max_bytes:
        raise ProtocolError("INPUT_TOO_LARGE", f"JSON input exceeds {max_bytes} bytes")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except json.JSONDecodeError as exc:
        raise ProtocolError("INVALID_JSON", exc.msg) from exc
    if _depth(value) > MAX_JSON_DEPTH:
        raise ProtocolError("JSON_TOO_DEEP", f"nesting exceeds {MAX_JSON_DEPTH}")
    return value


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("NOT_CANONICALIZABLE", "value is not canonical JSON data") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
