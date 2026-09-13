from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from interloc.protocol import ProtocolError, canonical_json, parse_request, request_digest, safe_mailbox_path, strict_loads, validate_artifact, validate_config_envelope, validate_index, validate_request, validate_response, validate_session_event, verify_payload_bytes

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def request(capability: str = "system.ping", *, arguments=None, ttl=timedelta(minutes=5), session_id=None):
    args = {"nonce": "hello"} if arguments is None else arguments
    value = {"schema_version": 1, "request_id": str(uuid4()), "mailbox_epoch": str(uuid4()), "target_device": "dev-box-1", "requester_label": "ordinary-chat", "created_at": NOW.isoformat().replace("+00:00", "Z"), "expires_at": (NOW + ttl).isoformat().replace("+00:00", "Z"), "capability": capability, "arguments": args}
    if session_id is not None:
        value["session_id"] = session_id
    return value


class ContractTests(unittest.TestCase):
    def assertCode(self, code: str, func, *args, **kwargs):
        with self.assertRaises(ProtocolError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def test_ping_round_trip_and_digest_determinism(self):
        normalized = validate_request(request(), now=NOW)
        self.assertEqual(normalized["arguments"], {"nonce": "hello"})
        left = request_digest(normalized)
        reordered = dict(reversed(list(normalized.items())))
        self.assertEqual(left, request_digest(reordered))
        self.assertEqual(canonical_json({"é": 1}), b'{"\xc3\xa9":1}')

    def test_strict_json_rejects_duplicate_keys_and_nonfinite(self):
        self.assertCode("DUPLICATE_KEY", strict_loads, '{"a":1,"a":2}')
        self.assertCode("INVALID_NUMBER", strict_loads, '{"a":NaN}')

    def test_utf8_and_input_size(self):
        self.assertCode("INVALID_UTF8", strict_loads, b"\xff")
        self.assertCode("INPUT_TOO_LARGE", strict_loads, " " * 100, max_bytes=10)

    def test_nested_json_limit(self):
        value = "0"
        for _ in range(40):
            value = "[" + value + "]"
        self.assertCode("JSON_TOO_DEEP", strict_loads, value)

    def test_unknown_schema_capability_and_extra_key(self):
        value = request(); value["schema_version"] = 2
        self.assertCode("UNKNOWN_SCHEMA_VERSION", validate_request, value, now=NOW)
        value = request(); value["capability"] = "shell.exec"
        self.assertCode("UNKNOWN_CAPABILITY", validate_request, value, now=NOW)
        value = request(); value["surprise"] = True
        self.assertCode("EXTRA_FIELD", validate_request, value, now=NOW)

    def test_expiry_and_clock_rules(self):
        self.assertCode("INVALID_EXPIRY", validate_request, request(ttl=timedelta(days=2)), now=NOW)
        expired = request(); expired["created_at"] = (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"); expired["expires_at"] = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        self.assertCode("EXPIRED", validate_request, expired, now=NOW)
        future = request(); future["created_at"] = (NOW + timedelta(seconds=61)).isoformat().replace("+00:00", "Z"); future["expires_at"] = (NOW + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        self.assertCode("CLOCK_SKEW", validate_request, future, now=NOW)
        self.assertCode("INVALID_EXPIRY", validate_request, request("capture.window", arguments={"window_id": str(uuid4()), "capture_scope_id": str(uuid4()), "format": "png"}, ttl=timedelta(minutes=3)), now=NOW)

    def test_terminal_tail_requires_session_and_bounds_lines(self):
        self.assertCode("MISSING_FIELD", validate_request, request("terminal.tail", arguments={}), now=NOW)
        sid = str(uuid4())
        good = validate_request(request("terminal.tail", arguments={"max_lines": 500}, session_id=sid), now=NOW)
        self.assertEqual(good["arguments"]["max_lines"], 500)
        self.assertCode("OUT_OF_RANGE", validate_request, request("terminal.tail", arguments={"max_lines": 501}, session_id=sid), now=NOW)

    def test_capture_is_strictly_scoped(self):
        good = validate_request(request("capture.window", arguments={"window_id": str(uuid4()), "capture_scope_id": str(uuid4()), "format": "png"}, ttl=timedelta(minutes=1)), now=NOW)
        self.assertEqual(good["arguments"]["format"], "png")
        bad = request("capture.window", arguments={"window_id": str(uuid4()), "capture_scope_id": str(uuid4()), "format": "jpg"}, ttl=timedelta(minutes=1))
        self.assertCode("UNSUPPORTED_VALUE", validate_request, bad, now=NOW)

    def test_safe_paths_reject_traversal_absolute_unc_and_ads(self):
        self.assertEqual(safe_mailbox_path("sessions/abc/chunk.txt"), "sessions/abc/chunk.txt")
        for bad in ("../secret", "/root/file", "C:\\secret", "\\\\server\\share", "dir/file:stream", "a//b"):
            self.assertCode("UNSAFE_PATH", safe_mailbox_path, bad)

    def test_artifact_location_and_payload_integrity(self):
        payload = "hello-é".encode()
        import hashlib
        descriptor = {"artifact_id": str(uuid4()), "media_type": "text/plain; charset=utf-8", "byte_length": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "redaction_status": "sanitized", "delivery_state": "local_only", "local_artifact_id": str(uuid4())}
        validated = validate_artifact(descriptor)
        verify_payload_bytes(payload, expected_length=validated["byte_length"], expected_sha256=validated["sha256"])
        self.assertCode("BYTE_LENGTH_MISMATCH", verify_payload_bytes, payload, expected_length=len(payload)+1, expected_sha256=validated["sha256"])
        self.assertCode("DIGEST_MISMATCH", verify_payload_bytes, payload, expected_length=len(payload), expected_sha256="0"*64)
        descriptor["mailbox_path"] = "artifacts/x.txt"
        self.assertCode("LOCATION_CARDINALITY", validate_artifact, descriptor)

    def test_response_index_event_and_config_envelopes(self):
        response = validate_response({"schema_version": 1, "request_id": str(uuid4()), "input_sha256": "a"*64, "outcome": "succeeded", "completed_at": NOW.isoformat().replace("+00:00", "Z"), "artifacts": []})
        self.assertEqual(response["outcome"], "succeeded")
        index = validate_index({"schema_version": 1, "group_id": str(uuid4()), "created_at": NOW.isoformat().replace("+00:00", "Z"), "entries": [{"kind": "text_chunk", "path": "sessions/s/chunks/a.txt", "sha256": "b"*64, "byte_length": 10}]})
        self.assertEqual(index["entries"][0]["byte_length"], 10)
        event = validate_session_event({"schema_version": 1, "event_id": str(uuid4()), "session_id": str(uuid4()), "sequence": 1, "occurred_at": NOW.isoformat().replace("+00:00", "Z"), "event_type": "stdout", "text": "✓"})
        self.assertEqual(event["text"], "✓")
        config = validate_config_envelope({"schema_version": 1, "device_id": "dev-box-1", "mailbox_epoch": str(uuid4())})
        self.assertEqual(config["device_id"], "dev-box-1")

    def test_parse_request_normalizes_rfc3339(self):
        value = request(); value["created_at"] = "2026-09-13T00:00:00+00:00"; value["expires_at"] = "2026-09-13T00:05:00+00:00"
        parsed = parse_request(json.dumps(value), now=NOW)
        self.assertTrue(parsed["created_at"].endswith("Z"))

    def test_schema_documents_are_valid_json_and_closed(self):
        root = Path(__file__).parents[2] / "schemas"
        names = {"request-v1.schema.json", "artifact-v1.schema.json", "response-v1.schema.json", "index-v1.schema.json", "session-event-v1.schema.json", "config-envelope-v1.schema.json"}
        self.assertTrue(names <= {p.name for p in root.glob("*.json")})
        for path in sorted(root.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(doc.get("additionalProperties", True), path.name)


if __name__ == "__main__":
    unittest.main()
