"""Read pinned-profile local Ansible status/result data without executing it.

The producer has hash-chained per-job status JSONL, not generic log messages.
Slot/generation are explicitly operator-mapped because per-job files do not
contain those fields. All external text reaches the existing evidence sanitizer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable, Sequence

from interloc.evidence import EvidenceGroup, build_text_group
from interloc.protocol import ProtocolError, strict_loads

CONTRACT = "ansible.execution.v1"
PROFILE = "interloc.ansible.status-chain.v1"
MAX_FILE = 256 * 1024
MAX_RECORD = 65536
TERMINAL = {"completed", "failed", "cancelled", "contained", "timeout", "rejected"}
TRANSITIONS = {
    None: {"created"}, "created": {"validated", "rejected", "failed"},
    "validated": {"admitted", "rejected", "failed"},
    "admitted": {"starting", "failed", "cancelled", "contained"},
    "starting": {"running", "failed", "cancelled", "contained", "timeout"},
    "running": {"cancelling", "completed", "failed", "cancelled", "contained", "timeout"},
    "cancelling": {"cancelled", "contained", "failed", "timeout"},
}


class TelemetryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _match(value, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _canonical(value) -> bytes:
    # Producer's canonical format is ASCII-escaped JSON, not Interloc's UTF-8 format.
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode(raw: bytes):
    try:
        value = strict_loads(raw, max_bytes=MAX_RECORD)
        count = 0
        def visit(item, depth=0):
            nonlocal count
            count += 1
            if depth > 20 or count > 4096 or type(item) is float:
                raise TelemetryError("MALFORMED_RECORD")
            if isinstance(item, str):
                item.encode("utf-8", "strict")
            elif isinstance(item, dict):
                for key, child in item.items():
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    visit(child, depth + 1)
        visit(value)
        return value
    except (ProtocolError, UnicodeError, ValueError, RecursionError):
        raise TelemetryError("MALFORMED_RECORD") from None


def _plain(path: Path) -> None:
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise TelemetryError("UNSAFE_SOURCE")


def _read(path: Path, maximum: int) -> tuple[bytes, tuple[int, int]]:
    _plain(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise TelemetryError("UNSAFE_SOURCE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise TelemetryError("SOURCE_CHANGED")
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise TelemetryError("SOURCE_TOO_LARGE")
    return data, (opened.st_dev, opened.st_ino)


@dataclass(frozen=True)
class Source:
    alias: str
    run_dir: Path
    job_id: str
    slot: int | None = None
    generation: int | None = None
    stale_seconds: int = 300
    contract: str = CONTRACT

    def validate(self) -> None:
        if (not _match(self.alias, r"[a-z][a-z0-9_-]{0,31}") or not _match(self.job_id, r"[0-9a-f]{32}")
                or not isinstance(self.run_dir, Path) or not self.run_dir.is_absolute()
                or self.run_dir.name != self.job_id):
            raise TelemetryError("MAPPING_INVALID")
        if ((self.slot is None) != (self.generation is None)
                or self.slot is not None and (type(self.slot) is not int or not 1 <= self.slot <= 4
                    or type(self.generation) is not int or not 1 <= self.generation <= 10**10 - 1)
                or type(self.stale_seconds) is not int or not 1 <= self.stale_seconds <= 86400):
            raise TelemetryError("MAPPING_INVALID")
        if self.contract != CONTRACT:
            raise TelemetryError("SCHEMA_MISMATCH")

    def binding(self) -> str:
        return _sha(_canonical({**asdict(self), "run_dir": str(self.run_dir)}))


@dataclass(frozen=True)
class Checkpoint:
    schema_version: int
    alias: str
    binding_sha256: str
    job_id: str
    slot: int | None
    generation: int | None
    file_id: tuple[int, int]
    offset: int
    sequence: int
    prefix_sha256: str
    metadata_sha256: str

    def encode(self) -> bytes:
        return _canonical(asdict(self))

    @classmethod
    def decode(cls, raw: bytes) -> "Checkpoint":
        value = _decode(raw)
        required = set(cls.__dataclass_fields__)
        if (type(value) is not dict or set(value) != required
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not _match(value["alias"], r"[a-z][a-z0-9_-]{0,31}")
                or not _match(value["job_id"], r"[0-9a-f]{32}")):
            raise TelemetryError("CHECKPOINT_INVALID")
        for field in ("binding_sha256", "prefix_sha256", "metadata_sha256"):
            if not _match(value[field], r"[0-9a-f]{64}"):
                raise TelemetryError("CHECKPOINT_INVALID")
        if (type(value["file_id"]) is not list or len(value["file_id"]) != 2
                or any(type(v) is not int or v < 0 for v in value["file_id"])
                or type(value["offset"]) is not int or not 0 <= value["offset"] <= MAX_FILE
                or type(value["sequence"]) is not int or not 0 <= value["sequence"] <= 32
                or (value["slot"] is None) != (value["generation"] is None)
                or value["slot"] is not None and (type(value["slot"]) is not int or not 1 <= value["slot"] <= 4
                    or type(value["generation"]) is not int or not 1 <= value["generation"] <= 10**10 - 1)):
            raise TelemetryError("CHECKPOINT_INVALID")
        return cls(**{**value, "file_id": tuple(value["file_id"])})


@dataclass(frozen=True)
class TelemetryBatch:
    diagnostic: str
    alias: str
    job_id: str
    slot: int | None
    generation: int | None
    slot_generation_origin: str
    offset: int
    record_count: int
    event_ids: tuple[str, ...]
    rotation: bool
    truncation: bool
    partial_record: bool
    stale: bool
    copy_status: str
    evidence_verified: bool
    group: EvidenceGroup
    checkpoint: bytes | None


def _metadata(raw: bytes) -> dict:
    value = _decode(raw)
    required = {"input_sha256", "contract_version", "implementation_version"}
    if (type(value) is not dict or not required <= value.keys() or value.keys() - required - {"source_fingerprint"}
            or value["contract_version"] != CONTRACT
            or not _match(value["input_sha256"], r"[0-9a-f]{64}")
            or not _match(value["implementation_version"], r"[A-Za-z0-9.+_-]{1,40}")
            or "source_fingerprint" in value and not _match(value["source_fingerprint"], r"[0-9a-f]{64}")):
        raise TelemetryError("SCHEMA_MISMATCH")
    return value


def _result(value, job_id: str) -> dict:
    fields = {"contract_version", "implementation_version", "job_id", "admission", "infrastructure",
              "transport", "worker_outcome", "evidence", "reason"}
    if (type(value) is not dict or set(value) != fields or value["contract_version"] != CONTRACT
            or value["job_id"] != job_id or not _match(value["implementation_version"], r"[A-Za-z0-9.+_-]{1,40}")
            or not _match(value["reason"], r"[A-Z0-9_]{1,80}")):
        raise TelemetryError("SCHEMA_MISMATCH")
    enums = {"admission": {"admitted", "rejected_schema", "rejected_policy", "rejected_runner", "rejected_capability"},
             "transport": {"not_applicable", "ready", "protocol_failed", "connection_failed", "retry_exhausted"},
             "worker_outcome": {"unknown", "success", "failed", "cancelled", "timeout", "provider_error", "worker_error", "no_result", "invalid_result", "contained"},
             "evidence": {"complete", "partial", "missing", "invalid"}}
    for name, values in enums.items():
        if not isinstance(value[name], str) or value[name] not in values:
            raise TelemetryError("SCHEMA_MISMATCH")
    infra = value["infrastructure"]
    if (type(infra) is not dict or set(infra) != {"state", "exit_code", "controller_completed"}
            or not isinstance(infra["state"], str)
            or infra["state"] not in {"not_started", "starting", "running", "completed", "controller_failed", "environment_failed", "terminated", "unknown"}
            or type(infra["controller_completed"]) is not bool
            or infra["exit_code"] is not None and type(infra["exit_code"]) is not int):
        raise TelemetryError("SCHEMA_MISMATCH")
    return value


def _events(raw: bytes, job_id: str) -> tuple[list[dict], int, bool]:
    end = raw.rfind(b"\n") + 1
    complete = raw[:end]
    previous, state, events, offset = "0" * 64, None, [], 0
    for line in complete.splitlines(keepends=True):
        value = _decode(line)
        if (type(value) is not dict or set(value) != {"seq", "prev", "event", "sha256"}
                or type(value["seq"]) is not int or value["seq"] != len(events) + 1
                or value["prev"] != previous or not _match(value["sha256"], r"[0-9a-f]{64}")):
            raise TelemetryError("CHAIN_INVALID")
        payload = {k: value[k] for k in ("seq", "prev", "event")}
        if _sha(_canonical(payload)) != value["sha256"]:
            raise TelemetryError("CHAIN_INVALID")
        event = value["event"]
        if (type(event) is not dict or not {"state", "time_ns"} <= event.keys()
                or event.keys() - {"state", "time_ns", "result"} or not isinstance(event["state"], str)
                or event["state"] not in TRANSITIONS.get(state, set())
                or type(event["time_ns"]) is not int or not 0 < event["time_ns"] < 2**63):
            raise TelemetryError("LIFECYCLE_INVALID")
        state = event["state"]
        if state in TERMINAL:
            _result(event.get("result"), job_id)
        elif "result" in event:
            raise TelemetryError("PREMATURE_RESULT")
        offset += len(line)
        events.append({**event, "sequence": value["seq"], "offset": offset, "event_id": _sha((job_id + value["sha256"]).encode())})
        previous = value["sha256"]
    if len(raw) - end > MAX_RECORD:
        raise TelemetryError("RECORD_TOO_LARGE")
    return events, end, end != len(raw)


class TelemetryReader:
    def __init__(self, source: Source, *, checkpoint: bytes | None = None,
                 secrets: Sequence[str] = (), personal_paths: Sequence[str] = (),
                 clock_ns: Callable[[], int] = time.time_ns, max_group_bytes: int = 256 * 1024) -> None:
        source.validate()
        if type(max_group_bytes) is not int or not 1 <= max_group_bytes <= 256 * 1024:
            raise TelemetryError("LIMIT_INVALID")
        self.source, self.clock_ns = source, clock_ns
        self.secrets, self.personal_paths, self.max_group_bytes = tuple(secrets), tuple(personal_paths), max_group_bytes
        self.checkpoint = Checkpoint.decode(checkpoint) if checkpoint is not None else None
        if self.checkpoint is not None:
            cp = self.checkpoint
            if cp.alias != source.alias or cp.slot != source.slot:
                raise TelemetryError("CHECKPOINT_SCOPE")
            if source.generation is not None and source.generation < cp.generation:
                raise TelemetryError("STALE_GENERATION")
            if cp.job_id == source.job_id and cp.binding_sha256 != source.binding():
                raise TelemetryError("MAPPING_CHANGED")

    def _batch(self, code: str, *, events=(), offset=0, rotation=False, truncation=False,
               partial=False, stale=False, copy_status="not_observed", checkpoint=None) -> TelemetryBatch:
        source = self.source
        fragments = [f"Telemetry only; no execution or success certification. profile={PROFILE}\n",
                     f"source={source.alias} job={source.job_id} contract={CONTRACT} diagnostic={code}\n",
                     f"mapped_slot={source.slot} mapped_generation={source.generation} origin={'operator_mapping' if source.slot is not None else 'unavailable'}\n",
                     f"offset={offset} rotation={rotation} truncation={truncation} partial={partial} stale={stale} result_copy={copy_status}\n"]
        for event in events:
            fragments.append(f"sequence={event['sequence']} event={event['event_id']} reported_state={event['state']} time_ns={event['time_ns']}\n")
            if "result" in event:
                value = event["result"]
                fragments.append("source-reported, evidence NOT revalidated: " + _canonical(value).decode("ascii") + "\n")
        group = build_text_group(fragments, secrets=self.secrets,
                                 personal_paths=(*self.personal_paths, str(source.run_dir)), max_group_bytes=self.max_group_bytes)
        return TelemetryBatch(code, source.alias, source.job_id, source.slot, source.generation,
                              "operator_mapping" if source.slot is not None else "unavailable", offset,
                              len(events), tuple(e["event_id"] for e in events), rotation, truncation, partial,
                              stale, copy_status, False, group, checkpoint)

    def poll(self) -> TelemetryBatch:
        """Validate the bounded complete chain on each poll; never follow result paths.

        The returned checkpoint is a proposal: persist the evidence batch before
        or atomically with it. On interruption, repeated event IDs enable dedupe.
        No checkpoint is advanced after malformed or incompatible source bytes.
        """
        old = self.checkpoint
        try:
            metadata_raw, _ = _read(self.source.run_dir / "metadata.json", MAX_RECORD)
            _metadata(metadata_raw)
            raw, file_id = _read(self.source.run_dir / "status.jsonl", MAX_FILE)
            events, offset, partial = _events(raw, self.source.job_id)
            metadata_sha = _sha(metadata_raw)
            rotation = old is not None and (old.job_id != self.source.job_id or old.file_id != file_id)
            truncation = old is not None and old.job_id == self.source.job_id and offset < old.offset
            if old is not None and old.job_id == self.source.job_id and old.metadata_sha256 != metadata_sha:
                raise TelemetryError("METADATA_CHANGED")
            if old is not None and not rotation and not truncation and _sha(raw[:old.offset]) != old.prefix_sha256:
                raise TelemetryError("PREFIX_CHANGED")
            start = 0 if old is None or rotation or truncation else old.sequence
            if start > len(events):
                raise TelemetryError("CHECKPOINT_INVALID")
            if old is not None and not rotation and not truncation:
                expected_offset = events[start - 1]["offset"] if start else 0
                if old.offset != expected_offset:
                    raise TelemetryError("CHECKPOINT_INVALID")
            now = self.clock_ns()
            if type(now) is not int or now <= 0:
                raise TelemetryError("CLOCK_INVALID")
            stale = bool(events and now - events[-1]["time_ns"] > self.source.stale_seconds * 10**9)
            if events and events[-1]["time_ns"] > now + 30 * 10**9:
                raise TelemetryError("CLOCK_SKEW")
            copy_status = "not_terminal"
            if events and events[-1]["state"] in TERMINAL:
                try:
                    copy_raw, _ = _read(self.source.run_dir / "result.json", MAX_RECORD)
                    copied = _result(_decode(copy_raw), self.source.job_id)
                    if copied != events[-1]["result"]:
                        raise TelemetryError("RESULT_COPY_MISMATCH")
                    copy_status = "matches_terminal_record_unverified"
                except FileNotFoundError:
                    copy_status = "missing"
            proposed = Checkpoint(1, self.source.alias, self.source.binding(), self.source.job_id,
                                  self.source.slot, self.source.generation, file_id, offset, len(events),
                                  _sha(raw[:offset]), metadata_sha)
            code = "STALE" if stale else "PARTIAL_RECORD" if partial else "OK"
            return self._batch(code, events=events[start:], offset=offset, rotation=rotation, truncation=truncation,
                               partial=partial, stale=stale, copy_status=copy_status, checkpoint=proposed.encode())
        except FileNotFoundError:
            return self._batch("UNAVAILABLE", checkpoint=old.encode() if old else None)
        except (TelemetryError, OSError) as exc:
            code = exc.code if isinstance(exc, TelemetryError) else "SOURCE_IO"
            return self._batch(code, checkpoint=old.encode() if old else None)

    def accept_checkpoint(self, checkpoint: bytes) -> None:
        """Call only after retaining the corresponding sanitized evidence batch."""
        candidate = Checkpoint.decode(checkpoint)
        if (candidate.binding_sha256 != self.source.binding() or candidate.alias != self.source.alias
                or candidate.job_id != self.source.job_id or candidate.slot != self.source.slot
                or candidate.generation != self.source.generation):
            raise TelemetryError("CHECKPOINT_SCOPE")
        if (self.checkpoint is not None and candidate.job_id == self.checkpoint.job_id
                and candidate.generation is not None and candidate.generation < self.checkpoint.generation):
            raise TelemetryError("STALE_GENERATION")
        self.checkpoint = candidate
