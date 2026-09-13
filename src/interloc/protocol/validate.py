"""Version-1 Interloc protocol validation and normalization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping
from uuid import UUID

from .errors import ProtocolError
from .reads import READ_CAPABILITIES, read_arguments
from .jsonutil import canonical_json, canonical_sha256, strict_loads

REQUEST_MAX_BYTES = 32 * 1024
MAX_TTL = timedelta(hours=24)
CAPTURE_MAX_TTL = timedelta(minutes=2)
FUTURE_SKEW = timedelta(seconds=60)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SAFE_CURSOR_RE = re.compile(r"^[A-Za-z0-9._~:-]{1,256}$")
OUTCOMES = {"succeeded", "rejected", "expired", "cancelled", "failed", "indeterminate"}
DELIVERY_STATES = {"local_only", "manual_attachment_pending", "remote_image_verified"}
REDACTION_STATES = {"not_applicable", "sanitized", "review_required"}


def _object(value: Any, *, required: set[str], optional: set[str] = set(), path: str = "$") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("TYPE_ERROR", "expected object", path=path)
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise ProtocolError("MISSING_FIELD", f"missing {sorted(missing)}", path=path)
    if extra:
        raise ProtocolError("EXTRA_FIELD", f"unexpected {sorted(extra)}", path=path)
    return value


def _string(value: Any, *, min_len: int = 1, max_len: int, path: str) -> str:
    if not isinstance(value, str) or not (min_len <= len(value) <= max_len):
        raise ProtocolError("INVALID_STRING", f"expected string length {min_len}..{max_len}", path=path)
    if any(ord(ch) < 0x20 for ch in value):
        raise ProtocolError("CONTROL_CHARACTER", "control characters are forbidden", path=path)
    return value


def _uuid(value: Any, *, path: str) -> str:
    text = _string(value, max_len=64, path=path)
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ProtocolError("INVALID_UUID", "expected UUID", path=path) from exc
    return str(parsed)


def _utc(value: Any, *, path: str) -> datetime:
    text = _string(value, max_len=40, path=path)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ProtocolError("INVALID_TIMESTAMP", "expected RFC3339 UTC timestamp", path=path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ProtocolError("INVALID_TIMESTAMP", "timestamp must be UTC", path=path)
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha(value: Any, *, path: str) -> str:
    text = _string(value, min_len=64, max_len=64, path=path)
    if not SHA256_RE.fullmatch(text):
        raise ProtocolError("INVALID_SHA256", "expected 64 lowercase hexadecimal characters", path=path)
    return text


def safe_mailbox_path(value: Any, *, path: str = "$.path") -> str:
    text = _string(value, max_len=512, path=path)
    normalized = text.replace("\\", "/")
    if text.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", text):
        raise ProtocolError("UNSAFE_PATH", "absolute paths are forbidden", path=path)
    if normalized.startswith("//") or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ProtocolError("UNSAFE_PATH", "empty, dot, traversal or UNC-like path components are forbidden", path=path)
    if ":" in text:
        raise ProtocolError("UNSAFE_PATH", "colon/alternate data stream syntax is forbidden", path=path)
    return normalized


def _arguments(capability: str, value: Any, session_id: str | None) -> dict[str, Any]:
    path = "$.arguments"
    if capability in READ_CAPABILITIES:
        return read_arguments(capability, value)
    if capability == "system.ping":
        obj = _object(value, required={"nonce"}, path=path)
        return {"nonce": _string(obj["nonce"], max_len=128, path=f"{path}.nonce")}
    if capability == "terminal.tail":
        if session_id is None:
            raise ProtocolError("MISSING_FIELD", "terminal.tail requires top-level session_id", path="$.session_id")
        obj = _object(value, required=set(), optional={"cursor", "max_lines"}, path=path)
        result: dict[str, Any] = {"max_lines": 100}
        if "cursor" in obj:
            cursor = _string(obj["cursor"], max_len=256, path=f"{path}.cursor")
            if not SAFE_CURSOR_RE.fullmatch(cursor):
                raise ProtocolError("INVALID_CURSOR", "cursor contains forbidden characters", path=f"{path}.cursor")
            result["cursor"] = cursor
        if "max_lines" in obj:
            lines = obj["max_lines"]
            if type(lines) is not int or not (1 <= lines <= 500):
                raise ProtocolError("OUT_OF_RANGE", "max_lines must be 1..500", path=f"{path}.max_lines")
            result["max_lines"] = lines
        return result
    if capability == "window.list":
        obj = _object(value, required={"capture_scope_id"}, path=path)
        return {"capture_scope_id": _uuid(obj["capture_scope_id"], path=f"{path}.capture_scope_id")}
    if capability == "capture.window":
        obj = _object(value, required={"window_id", "capture_scope_id", "format"}, path=path)
        if obj["format"] != "png":
            raise ProtocolError("UNSUPPORTED_VALUE", "capture format must be png", path=f"{path}.format")
        return {"window_id": _uuid(obj["window_id"], path=f"{path}.window_id"), "capture_scope_id": _uuid(obj["capture_scope_id"], path=f"{path}.capture_scope_id"), "format": "png"}
    raise ProtocolError("UNKNOWN_CAPABILITY", f"unsupported capability {capability!r}", path="$.capability")


def validate_request(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    obj = _object(value, required={"schema_version", "request_id", "mailbox_epoch", "target_device", "requester_label", "created_at", "expires_at", "capability", "arguments"}, optional={"session_id"})
    if obj["schema_version"] != 1:
        raise ProtocolError("UNKNOWN_SCHEMA_VERSION", "request schema_version must be 1", path="$.schema_version")
    request_id = _uuid(obj["request_id"], path="$.request_id")
    mailbox_epoch = _uuid(obj["mailbox_epoch"], path="$.mailbox_epoch")
    target = _string(obj["target_device"], max_len=64, path="$.target_device")
    if not SAFE_DEVICE_RE.fullmatch(target):
        raise ProtocolError("INVALID_DEVICE_ID", "target_device contains forbidden characters", path="$.target_device")
    requester = _string(obj["requester_label"], max_len=128, path="$.requester_label")
    created = _utc(obj["created_at"], path="$.created_at")
    expires = _utc(obj["expires_at"], path="$.expires_at")
    if expires <= created:
        raise ProtocolError("INVALID_EXPIRY", "expires_at must be after created_at", path="$.expires_at")
    ttl = expires - created
    if ttl > MAX_TTL:
        raise ProtocolError("INVALID_EXPIRY", "request lifetime exceeds 24 hours", path="$.expires_at")
    capability = _string(obj["capability"], max_len=64, path="$.capability")
    if capability == "capture.window" and ttl > CAPTURE_MAX_TTL:
        raise ProtocolError("INVALID_EXPIRY", "capture.window lifetime exceeds two minutes", path="$.expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if created > current + FUTURE_SKEW:
        raise ProtocolError("CLOCK_SKEW", "created_at is too far in the future", path="$.created_at")
    if expires <= current:
        raise ProtocolError("EXPIRED", "request has expired", path="$.expires_at")
    session_id = _uuid(obj["session_id"], path="$.session_id") if "session_id" in obj else None
    arguments = _arguments(capability, obj["arguments"], session_id)
    result: dict[str, Any] = {"schema_version": 1, "request_id": request_id, "mailbox_epoch": mailbox_epoch, "target_device": target, "requester_label": requester, "created_at": _utc_text(created), "expires_at": _utc_text(expires), "capability": capability, "arguments": arguments}
    if session_id is not None:
        result["session_id"] = session_id
    return result


def parse_request(data: bytes | str, *, now: datetime | None = None) -> dict[str, Any]:
    return validate_request(strict_loads(data, max_bytes=REQUEST_MAX_BYTES), now=now)


def request_digest(request: Mapping[str, Any]) -> str:
    return canonical_sha256(validate_request(dict(request), now=_utc(request["created_at"], path="$.created_at")))


def validate_artifact(value: Any, *, path: str = "$.artifacts[]") -> dict[str, Any]:
    obj = _object(value, required={"artifact_id", "media_type", "byte_length", "sha256", "redaction_status", "delivery_state"}, optional={"mailbox_path", "local_artifact_id"}, path=path)
    length = obj["byte_length"]
    if type(length) is not int or not (0 <= length <= 32 * 1024 * 1024):
        raise ProtocolError("OUT_OF_RANGE", "artifact byte_length must be 0..32MiB", path=f"{path}.byte_length")
    redaction = obj["redaction_status"]
    if redaction not in REDACTION_STATES:
        raise ProtocolError("UNSUPPORTED_VALUE", "invalid redaction_status", path=f"{path}.redaction_status")
    delivery = obj["delivery_state"]
    if delivery not in DELIVERY_STATES:
        raise ProtocolError("UNSUPPORTED_VALUE", "invalid delivery_state", path=f"{path}.delivery_state")
    has_remote = "mailbox_path" in obj
    has_local = "local_artifact_id" in obj
    if has_remote == has_local:
        raise ProtocolError("LOCATION_CARDINALITY", "exactly one artifact location is required", path=path)
    result: dict[str, Any] = {"artifact_id": _uuid(obj["artifact_id"], path=f"{path}.artifact_id"), "media_type": _string(obj["media_type"], max_len=127, path=f"{path}.media_type"), "byte_length": length, "sha256": _sha(obj["sha256"], path=f"{path}.sha256"), "redaction_status": redaction, "delivery_state": delivery}
    if has_remote:
        result["mailbox_path"] = safe_mailbox_path(obj["mailbox_path"], path=f"{path}.mailbox_path")
    else:
        result["local_artifact_id"] = _uuid(obj["local_artifact_id"], path=f"{path}.local_artifact_id")
    return result


def validate_response(value: Any) -> dict[str, Any]:
    obj = _object(value, required={"schema_version", "request_id", "input_sha256", "outcome", "completed_at", "artifacts"}, optional={"error"})
    if obj["schema_version"] != 1:
        raise ProtocolError("UNKNOWN_SCHEMA_VERSION", "response schema_version must be 1", path="$.schema_version")
    outcome = obj["outcome"]
    if outcome not in OUTCOMES:
        raise ProtocolError("UNSUPPORTED_VALUE", "invalid outcome", path="$.outcome")
    artifacts = obj["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > 128:
        raise ProtocolError("OUT_OF_RANGE", "artifacts must be a list of at most 128 items", path="$.artifacts")
    result: dict[str, Any] = {"schema_version": 1, "request_id": _uuid(obj["request_id"], path="$.request_id"), "input_sha256": _sha(obj["input_sha256"], path="$.input_sha256"), "outcome": outcome, "completed_at": _utc_text(_utc(obj["completed_at"], path="$.completed_at")), "artifacts": [validate_artifact(item, path=f"$.artifacts[{i}]") for i, item in enumerate(artifacts)]}
    if "error" in obj:
        err = _object(obj["error"], required={"code", "message"}, path="$.error")
        result["error"] = {"code": _string(err["code"], max_len=64, path="$.error.code"), "message": _string(err["message"], min_len=0, max_len=512, path="$.error.message")}
    return result


def verify_payload_bytes(payload: bytes, *, expected_length: int, expected_sha256: str, path: str = "$.artifact") -> None:
    import hashlib
    if len(payload) != expected_length:
        raise ProtocolError("BYTE_LENGTH_MISMATCH", "payload length does not match descriptor", path=path)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != _sha(expected_sha256, path=f"{path}.sha256"):
        raise ProtocolError("DIGEST_MISMATCH", "payload SHA-256 does not match descriptor", path=path)


def validate_index(value: Any) -> dict[str, Any]:
    obj = _object(value, required={"schema_version", "group_id", "created_at", "entries"})
    if obj["schema_version"] != 1:
        raise ProtocolError("UNKNOWN_SCHEMA_VERSION", "index schema_version must be 1", path="$.schema_version")
    entries = obj["entries"]
    if not isinstance(entries, list) or not (1 <= len(entries) <= 128):
        raise ProtocolError("OUT_OF_RANGE", "entries must contain 1..128 items", path="$.entries")
    normalized = []
    for i, item in enumerate(entries):
        path = f"$.entries[{i}]"
        ent = _object(item, required={"kind", "path", "sha256", "byte_length"}, path=path)
        if ent["kind"] not in {"response", "session_event", "text_chunk"}:
            raise ProtocolError("UNSUPPORTED_VALUE", "invalid index entry kind", path=f"{path}.kind")
        length = ent["byte_length"]
        if type(length) is not int or not (0 <= length <= 256 * 1024):
            raise ProtocolError("OUT_OF_RANGE", "entry byte_length must be 0..256KiB", path=f"{path}.byte_length")
        normalized.append({"kind": ent["kind"], "path": safe_mailbox_path(ent["path"], path=f"{path}.path"), "sha256": _sha(ent["sha256"], path=f"{path}.sha256"), "byte_length": length})
    return {"schema_version": 1, "group_id": _uuid(obj["group_id"], path="$.group_id"), "created_at": _utc_text(_utc(obj["created_at"], path="$.created_at")), "entries": normalized}


def validate_session_event(value: Any) -> dict[str, Any]:
    obj = _object(value, required={"schema_version", "event_id", "session_id", "sequence", "occurred_at", "event_type"}, optional={"text", "exit_code", "detail"})
    if obj["schema_version"] != 1:
        raise ProtocolError("UNKNOWN_SCHEMA_VERSION", "event schema_version must be 1", path="$.schema_version")
    event_type = obj["event_type"]
    allowed = {"command_start", "command_end", "stdout", "stderr", "gap", "rotation", "timeout", "cancelled"}
    if event_type not in allowed:
        raise ProtocolError("UNSUPPORTED_VALUE", "invalid event_type", path="$.event_type")
    seq = obj["sequence"]
    if type(seq) is not int or not (0 <= seq <= 2**63 - 1):
        raise ProtocolError("OUT_OF_RANGE", "sequence must be a non-negative 63-bit integer", path="$.sequence")
    result: dict[str, Any] = {"schema_version": 1, "event_id": _uuid(obj["event_id"], path="$.event_id"), "session_id": _uuid(obj["session_id"], path="$.session_id"), "sequence": seq, "occurred_at": _utc_text(_utc(obj["occurred_at"], path="$.occurred_at")), "event_type": event_type}
    if event_type in {"stdout", "stderr"}:
        if "text" not in obj:
            raise ProtocolError("MISSING_FIELD", "stream events require text", path="$.text")
        result["text"] = _string(obj["text"], min_len=0, max_len=32 * 1024, path="$.text")
    elif "text" in obj:
        raise ProtocolError("EXTRA_FIELD", "text is only valid for stdout/stderr", path="$.text")
    if event_type == "command_end":
        if "exit_code" not in obj:
            raise ProtocolError("MISSING_FIELD", "command_end requires exit_code", path="$.exit_code")
        code = obj["exit_code"]
        if code is not None and (type(code) is not int or not (-2**31 <= code <= 2**31 - 1)):
            raise ProtocolError("OUT_OF_RANGE", "exit_code must be int32 or null", path="$.exit_code")
        result["exit_code"] = code
    elif "exit_code" in obj:
        raise ProtocolError("EXTRA_FIELD", "exit_code is only valid for command_end", path="$.exit_code")
    if "detail" in obj:
        result["detail"] = _string(obj["detail"], min_len=0, max_len=512, path="$.detail")
    return result


def validate_config_envelope(value: Any) -> dict[str, Any]:
    obj = _object(value, required={"schema_version", "device_id", "mailbox_epoch"}, optional={"profile"})
    if obj["schema_version"] != 1:
        raise ProtocolError("UNKNOWN_SCHEMA_VERSION", "config schema_version must be 1", path="$.schema_version")
    device = _string(obj["device_id"], max_len=64, path="$.device_id")
    if not SAFE_DEVICE_RE.fullmatch(device):
        raise ProtocolError("INVALID_DEVICE_ID", "device_id contains forbidden characters", path="$.device_id")
    result = {"schema_version": 1, "device_id": device, "mailbox_epoch": _uuid(obj["mailbox_epoch"], path="$.mailbox_epoch")}
    if "profile" in obj:
        result["profile"] = _string(obj["profile"], max_len=64, path="$.profile")
    return result


__all__ = ["ProtocolError", "canonical_json", "canonical_sha256", "parse_request", "request_digest", "safe_mailbox_path", "strict_loads", "validate_artifact", "validate_config_envelope", "validate_index", "validate_request", "validate_response", "validate_session_event", "verify_payload_bytes"]
