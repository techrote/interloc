from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from interloc.evidence import text_group_export
from interloc.integrations.ansible.read import (
    CONTRACT, MAX_FILE, MAX_RECORD, Checkpoint, Source, TelemetryError, TelemetryReader,
)

NOW = 1_789_260_000_000_000_000


def encode(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")


def result(job):
    return {"contract_version": CONTRACT, "implementation_version": "0.1.3", "job_id": job,
            "admission": "admitted", "infrastructure": {"state": "completed", "exit_code": 0, "controller_completed": True},
            "transport": "ready", "worker_outcome": "success", "evidence": "complete", "reason": "PREDICATE_PASSED"}


def chain(events):
    previous = "0" * 64
    lines = []
    for seq, event in enumerate(events, 1):
        payload = {"seq": seq, "prev": previous, "event": event}
        previous = hashlib.sha256(encode(payload)).hexdigest()
        lines.append(encode({**payload, "sha256": previous}) + b"\n")
    return b"".join(lines)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="interloc telemetry ")
        self.root = Path(self.temp.name)
        self.job = uuid4().hex
        self.run = self.root / self.job
        self.run.mkdir()
        self.metadata = {"input_sha256": "a" * 64, "source_fingerprint": "b" * 64,
                         "contract_version": CONTRACT, "implementation_version": "0.1.3"}
        (self.run / "metadata.json").write_bytes(encode(self.metadata))
        self.events = [{"state": "created", "time_ns": NOW}]
        self.write()
        self.source = Source("slot1", self.run, self.job, 1, 7)
        self.reader = TelemetryReader(self.source, clock_ns=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        (self.run / "status.jsonl").write_bytes(chain(self.events))

    def append(self, state, **kw):
        self.events.append({"state": state, "time_ns": NOW, **kw})
        self.write()

    def finish(self, **kw):
        for state in ("validated", "admitted", "starting", "running"):
            self.append(state)
        value = {**result(self.job), **kw}
        self.append("completed", result=value)
        (self.run / "result.json").write_bytes(encode(value))
        return value

    def accept(self):
        batch = self.reader.poll()
        self.reader.accept_checkpoint(batch.checkpoint)
        return batch

    def text(self, batch):
        return "".join(chunk.text for chunk in batch.group.chunks)

    def code(self, expected, call, *args, **kw):
        with self.assertRaises(TelemetryError) as ctx:
            call(*args, **kw)
        self.assertEqual(ctx.exception.code, expected)

    def test_valid_chain_preserves_alias_run_scope_offsets_and_unverified_axes(self):
        self.finish()
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "OK")
        self.assertEqual(batch.record_count, 6)
        self.assertEqual(batch.alias, "slot1")
        self.assertEqual(batch.job_id, self.job)
        self.assertEqual((batch.slot, batch.generation), (1, 7))
        self.assertEqual(batch.slot_generation_origin, "operator_mapping")
        self.assertEqual(batch.offset, (self.run / "status.jsonl").stat().st_size)
        self.assertEqual(batch.copy_status, "matches_terminal_record_unverified")
        self.assertFalse(batch.evidence_verified)
        self.assertIn('"worker_outcome":"success"', self.text(batch))
        self.assertIn("evidence NOT revalidated", self.text(batch))
        self.assertNotIn(str(self.run), self.text(batch))

    def test_checkpoint_append_and_restart_deliver_only_new_events(self):
        first = self.accept()
        self.assertEqual(self.reader.poll().record_count, 0)
        self.append("validated")
        second = self.accept()
        self.assertEqual(second.record_count, 1)
        self.assertNotEqual(first.event_ids, second.event_ids)
        resumed = TelemetryReader(self.source, checkpoint=second.checkpoint, clock_ns=lambda: NOW)
        self.assertEqual(resumed.poll().record_count, 0)
        self.append("admitted")
        self.assertEqual(resumed.poll().record_count, 1)
        cp = Checkpoint.decode(second.checkpoint)
        self.assertEqual(cp.sequence, 2)
        self.assertEqual(cp.encode(), second.checkpoint)

    def test_unaccepted_checkpoint_repeats_stable_event_ids(self):
        first, repeat = self.reader.poll(), self.reader.poll()
        self.assertEqual(first.event_ids, repeat.event_ids)
        self.assertEqual(first.checkpoint, repeat.checkpoint)
        self.assertIsNone(self.reader.checkpoint)

    def test_unmapped_slot_generation_remain_unavailable_through_restart(self):
        source = replace(self.source, slot=None, generation=None)
        reader = TelemetryReader(source, clock_ns=lambda: NOW)
        batch = reader.poll()
        self.assertEqual(batch.slot_generation_origin, "unavailable")
        self.assertIsNone(batch.slot)
        self.assertIn("origin=unavailable", self.text(batch))
        reader.accept_checkpoint(batch.checkpoint)
        reader.accept_checkpoint(reader.poll().checkpoint)  # None is not an ordered integer.
        self.assertEqual(reader.poll().record_count, 0)

    def test_rotation_revalidates_chain_and_retains_stable_dedupe_ids(self):
        first = self.accept()
        path = self.run / "status.jsonl"
        moved = self.run / "rotated.jsonl"
        path.rename(moved)
        self.write()
        batch = self.reader.poll()
        self.assertTrue(batch.rotation)
        self.assertEqual(batch.event_ids, first.event_ids)
        self.assertEqual(batch.record_count, 1)

    def test_truncation_is_explicit_and_restarts_validated_prefix(self):
        self.append("validated")
        self.accept()
        self.events.pop()
        self.write()
        batch = self.reader.poll()
        self.assertTrue(batch.truncation)
        self.assertFalse(batch.rotation)
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.diagnostic, "OK")

    def test_regrown_rewritten_prefix_is_rejected_without_cursor_advance(self):
        first = self.accept()
        self.events[0]["time_ns"] += 1
        self.append("validated")
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "PREFIX_CHANGED")
        self.assertEqual(batch.record_count, 0)
        self.assertEqual(batch.checkpoint, first.checkpoint)

    def test_torn_line_waits_for_completion_without_advancing_past_it(self):
        first = self.accept()
        self.events.append({"state": "validated", "time_ns": NOW})
        raw = chain(self.events)
        (self.run / "status.jsonl").write_bytes(raw[:-5])
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "PARTIAL_RECORD")
        self.assertTrue(batch.partial_record)
        self.assertEqual(batch.record_count, 0)
        self.assertEqual(batch.offset, first.offset)
        (self.run / "status.jsonl").write_bytes(raw)
        self.assertEqual(self.reader.poll().record_count, 1)

    def test_empty_chain_is_no_result_not_success(self):
        (self.run / "status.jsonl").write_bytes(b"")
        batch = self.reader.poll()
        self.assertEqual(batch.record_count, 0)
        self.assertFalse(batch.evidence_verified)
        self.assertEqual(batch.copy_status, "not_terminal")
        self.reader.accept_checkpoint(batch.checkpoint)
        self.write()
        self.assertEqual(self.reader.poll().record_count, 1)

    def test_missing_installation_does_not_initialize_any_files(self):
        missing_job = uuid4().hex
        missing = self.root / "no-installation" / missing_job
        source = Source("missing", missing, missing_job)
        before = set(self.root.iterdir())
        batch = TelemetryReader(source).poll()
        self.assertEqual(batch.diagnostic, "UNAVAILABLE")
        self.assertIsNone(batch.checkpoint)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse(missing.parent.exists())

    def test_stale_and_future_source_times_are_distinguished(self):
        stale = TelemetryReader(self.source, clock_ns=lambda: NOW + 301 * 10**9).poll()
        self.assertTrue(stale.stale)
        self.assertEqual(stale.diagnostic, "STALE")
        self.events[0]["time_ns"] = NOW + 31 * 10**9
        self.write()
        self.assertEqual(self.reader.poll().diagnostic, "CLOCK_SKEW")
        self.assertEqual(TelemetryReader(self.source, clock_ns=lambda: True).poll().diagnostic, "CLOCK_INVALID")

    def test_generation_rollback_rejected_and_newer_job_is_explicit_rotation(self):
        first = self.accept()
        self.code("STALE_GENERATION", TelemetryReader, replace(self.source, generation=6), checkpoint=first.checkpoint)
        new_job = uuid4().hex
        new_run = self.root / new_job
        new_run.mkdir()
        (new_run / "metadata.json").write_bytes(encode(self.metadata))
        (new_run / "status.jsonl").write_bytes(chain(self.events))
        source = replace(self.source, generation=8, job_id=new_job, run_dir=new_run)
        batch = TelemetryReader(source, checkpoint=first.checkpoint, clock_ns=lambda: NOW).poll()
        self.assertTrue(batch.rotation)
        self.assertEqual(batch.generation, 8)
        self.assertEqual(batch.job_id, new_job)
        self.assertNotEqual(first.event_ids, batch.event_ids)

    def test_mapping_and_checkpoint_scope_changes_are_refused(self):
        first = self.accept()
        self.code("CHECKPOINT_SCOPE", TelemetryReader, replace(self.source, alias="other"), checkpoint=first.checkpoint)
        self.code("CHECKPOINT_SCOPE", TelemetryReader, replace(self.source, slot=2), checkpoint=first.checkpoint)
        self.code("MAPPING_CHANGED", TelemetryReader, replace(self.source, stale_seconds=1), checkpoint=first.checkpoint)
        altered = replace(Checkpoint.decode(first.checkpoint), job_id="a" * 32)
        self.code("CHECKPOINT_SCOPE", self.reader.accept_checkpoint, altered.encode())

    def test_checkpoint_boundary_mismatch_cannot_skip_an_event(self):
        self.append("validated")
        first = self.reader.poll()
        cp = replace(Checkpoint.decode(first.checkpoint), sequence=1)
        restarted = TelemetryReader(self.source, checkpoint=cp.encode(), clock_ns=lambda: NOW)
        self.assertEqual(restarted.poll().diagnostic, "CHECKPOINT_INVALID")

    def test_invalid_checkpoint_types_and_unknown_fields_rejected(self):
        value = json.loads(self.reader.poll().checkpoint)
        for key, bad in (("sequence", True), ("offset", MAX_FILE + 1), ("file_id", [-1, 2]),
                         ("schema_version", True), ("generation", None), ("prefix_sha256", "not-hash")):
            with self.subTest(key=key):
                self.code("CHECKPOINT_INVALID", Checkpoint.decode, encode({**value, key: bad}))
        self.code("CHECKPOINT_INVALID", Checkpoint.decode, encode({**value, "command": "inert"}))

    def test_changed_metadata_invalidates_previous_checkpoint(self):
        first = self.accept()
        self.metadata["input_sha256"] = "c" * 64
        (self.run / "metadata.json").write_bytes(encode(self.metadata))
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "METADATA_CHANGED")
        self.assertEqual(batch.checkpoint, first.checkpoint)

    def test_unknown_metadata_contract_fails_without_moving_checkpoint(self):
        first = self.accept()
        self.metadata["contract_version"] = "ansible.execution.v99"
        (self.run / "metadata.json").write_bytes(encode(self.metadata))
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "SCHEMA_MISMATCH")
        self.assertEqual(batch.checkpoint, first.checkpoint)
        self.assertEqual(batch.record_count, 0)

    def test_rejected_job_metadata_may_omit_source_fingerprint(self):
        self.metadata.pop("source_fingerprint")
        (self.run / "metadata.json").write_bytes(encode(self.metadata))
        value = {**result(self.job), "admission": "rejected_schema", "worker_outcome": "unknown", "reason": "INVALID_TYPE"}
        self.append("rejected", result=value)
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "OK")
        self.assertEqual(batch.copy_status, "missing")
        self.assertFalse(batch.evidence_verified)

    def test_terminal_copy_is_optional_but_cannot_contradict_status(self):
        value = self.finish()
        (self.run / "result.json").unlink()
        self.assertEqual(self.reader.poll().copy_status, "missing")
        (self.run / "result.json").write_bytes(encode({**value, "reason": "DIFFERENT"}))
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "RESULT_COPY_MISMATCH")
        self.assertIsNone(batch.checkpoint)
        self.assertEqual(batch.record_count, 0)

    def test_convenience_result_alone_is_not_terminal_evidence(self):
        (self.run / "result.json").write_bytes(encode(result(self.job)))
        batch = self.reader.poll()
        self.assertEqual(batch.copy_status, "not_terminal")
        self.assertNotIn('"worker_outcome":"success"', self.text(batch))
        self.assertFalse(batch.evidence_verified)

    def test_wrong_job_result_unknown_contract_and_infra_boolean_exit_fail_closed(self):
        self.finish()
        for key, bad in (("job_id", "0" * 32), ("contract_version", "ansible.execution.v99"),
                         ("infrastructure", {"state": "completed", "exit_code": True, "controller_completed": True}),
                         ("transport", ["ready"])):
            with self.subTest(key=key):
                self.events[-1]["result"] = {**result(self.job), key: bad}
                self.write()
                self.assertEqual(self.reader.poll().diagnostic, "SCHEMA_MISMATCH")

    def test_illegal_lifecycle_premature_and_missing_terminal_result_rejected(self):
        self.append("completed", result=result(self.job))
        self.assertEqual(self.reader.poll().diagnostic, "LIFECYCLE_INVALID")
        self.events = [{"state": "created", "time_ns": NOW, "result": result(self.job)}]
        self.write()
        self.assertEqual(self.reader.poll().diagnostic, "PREMATURE_RESULT")
        self.events = [{"state": "created", "time_ns": NOW}, {"state": "rejected", "time_ns": NOW}]
        self.write()
        self.assertEqual(self.reader.poll().diagnostic, "SCHEMA_MISMATCH")

    def test_chain_sequence_previous_and_hash_are_checked(self):
        original = json.loads(chain(self.events))
        for key, bad in (("seq", 2), ("seq", True), ("prev", "f" * 64), ("sha256", "f" * 64)):
            with self.subTest(key=key, value=bad):
                (self.run / "status.jsonl").write_bytes(encode({**original, key: bad}) + b"\n")
                self.assertEqual(self.reader.poll().diagnostic, "CHAIN_INVALID")

    def test_unknown_source_fields_are_data_errors_never_instructions(self):
        marker = self.root / "MUST_NOT_EXIST"
        self.metadata["command"] = f"write {marker}"
        (self.run / "metadata.json").write_bytes(encode(self.metadata))
        batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "SCHEMA_MISMATCH")
        self.assertFalse(marker.exists())
        self.assertNotIn("MUST_NOT_EXIST", self.text(batch))
        self.assertNotIn(str(self.root), self.text(batch))

    def test_duplicate_keys_float_nan_unicode_and_complexity_are_rejected(self):
        variants = [b'{"seq":1,"seq":1}\n', b'{"x":1.0}\n', b'{"x":NaN}\n',
                    b'{"x":"\\ud800"}\n', b'\xff\n', b'[' * 25 + b'0' + b']' * 25 + b'\n',
                    encode({"x": [0] * 5000}) + b"\n"]
        for raw in variants:
            with self.subTest(raw=raw[:32]):
                (self.run / "status.jsonl").write_bytes(raw)
                self.assertEqual(self.reader.poll().diagnostic, "MALFORMED_RECORD")

    def test_source_and_partial_record_size_limits(self):
        (self.run / "status.jsonl").write_bytes(b"x" * (MAX_FILE + 1))
        self.assertEqual(self.reader.poll().diagnostic, "SOURCE_TOO_LARGE")
        (self.run / "status.jsonl").write_bytes(b"x" * (MAX_RECORD + 1))
        self.assertEqual(self.reader.poll().diagnostic, "RECORD_TOO_LARGE")
        (self.run / "status.jsonl").write_bytes(b"x" * (MAX_RECORD + 1) + b"\n")
        self.assertEqual(self.reader.poll().diagnostic, "MALFORMED_RECORD")

    def test_source_reported_secrets_pass_through_existing_sanitizer_and_export(self):
        secret = "SYNTHETIC_PRIVATE_TOKEN"
        self.finish(reason=secret)
        reader = TelemetryReader(self.source, secrets=[secret], clock_ns=lambda: NOW)
        batch = reader.poll()
        files, manifest = text_group_export(batch.group, str(uuid4()))
        combined = b"".join(files.values())
        self.assertNotIn(secret.encode(), combined)
        self.assertIn(b"[REDACTED]", combined)
        self.assertFalse(manifest["truncated"])
        for entry in manifest["entries"]:
            self.assertEqual(hashlib.sha256(files[entry["path"]]).hexdigest(), entry["sha256"])
        self.assertEqual(batch.record_count, 6)

    def test_evidence_budget_truncation_is_explicit(self):
        self.finish()
        batch = TelemetryReader(self.source, max_group_bytes=50, clock_ns=lambda: NOW).poll()
        self.assertEqual(batch.group.sanitized_bytes, 50)
        self.assertTrue(batch.group.truncated)
        self.assertGreater(batch.group.dropped_sanitized_bytes, 0)
        for limit in (True, 0, 262145):
            self.code("LIMIT_INVALID", TelemetryReader, self.source, max_group_bytes=limit)

    def test_poll_does_not_change_source_bytes_or_directory_entries(self):
        self.finish()
        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        self.accept()
        self.reader.poll()
        after = {p.name: p.read_bytes() for p in self.run.iterdir()}
        self.assertEqual(before, after)

    def test_io_errors_do_not_echo_personal_paths(self):
        with patch("interloc.integrations.ansible.read.os.open", side_effect=PermissionError("PRIVATE/PATH")):
            batch = self.reader.poll()
        self.assertEqual(batch.diagnostic, "SOURCE_IO")
        self.assertNotIn("PRIVATE/PATH", self.text(batch))

    def test_plain_file_link_and_fifo_checks_prevent_following_or_blocking(self):
        path = self.run / "status.jsonl"
        outside = self.root / "private"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            os.link(outside, path)
        except OSError:
            self.skipTest("hardlinks unavailable")
        self.assertEqual(self.reader.poll().diagnostic, "UNSAFE_SOURCE")
        path.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(path)
            self.assertEqual(self.reader.poll().diagnostic, "UNSAFE_SOURCE")
            path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation requires Windows developer privileges")
        self.assertEqual(self.reader.poll().diagnostic, "UNSAFE_SOURCE")
        self.assertEqual(outside.read_bytes(), chain(self.events))

    def test_source_mapping_rejects_remote_like_paths_and_unsupported_contracts(self):
        for changes in ({"alias": "../escape"}, {"job_id": "../../private"}, {"run_dir": Path("relative")},
                        {"slot": True}, {"generation": 0}, {"slot": None}, {"stale_seconds": True}):
            with self.subTest(changes=changes):
                self.code("MAPPING_INVALID", TelemetryReader, replace(self.source, **changes))
        self.code("SCHEMA_MISMATCH", TelemetryReader, replace(self.source, contract="unknown"))


if __name__ == "__main__":
    unittest.main()
