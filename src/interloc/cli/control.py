"""Local control service. It has no network, clipboard or provider side effects."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import stat
from typing import Any, Callable, Mapping
from uuid import UUID

from interloc.broker import BrokerError, CapabilityRegistry, Dispatcher, Journal, RequestKey
from interloc.policy import Approval, Decision, Enrollment, Grant, PolicyError, TransportOrigin, evaluate, request_scope
from interloc.protocol import canonical_sha256, parse_request, strict_loads


class ControlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def read_local_file(path: Path, *, limit: int = 32768) -> bytes:
    """Bound explicitly selected local JSON, rejecting special files and links.

    This is local operator file selection, never a remote path capability.
    The same-account adversary is outside the security model.
    """
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or path.is_junction():
        raise ControlError("UNSAFE_LOCAL_FILE", "Select a regular local file, not a link or device.")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ControlError("INPUT_TOO_LARGE", "Local input exceeds its byte limit.")
    return data


def validate_home(path: Path) -> Path:
    if not path.is_absolute():
        raise ControlError("HOME_INVALID", "The local state root must be absolute.")
    # Reject junctions as well as links, including non-final path components.
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ControlError("HOME_INVALID", "The local state root cannot pass through a link.")
        if (parent / ".git").exists():
            raise ControlError("HOME_IN_WORKTREE", "Keep local runtime state outside project worktrees.")
    return path


def load_enrollment(path: Path) -> Enrollment:
    value = strict_loads(read_local_file(path), max_bytes=32768)
    required = {"schema_version", "repository_id", "mailbox_epoch", "target_device", "private_repository", "inbox_branch", "outbox_branch", "revision", "grants"}
    if not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ControlError("CONFIG_INVALID", "Enrollment must use the exact version-1 local configuration.")
    grants = value["grants"]
    if not isinstance(grants, list) or len(grants) > 128:
        raise ControlError("CONFIG_INVALID", "Enrollment grants must be a bounded list.")
    parsed = []
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) != {"capability", "scope_id", "decision", "destination"}:
            raise ControlError("CONFIG_INVALID", "A grant has missing or unknown fields.")
        if not all(isinstance(x, str) and x.isprintable() for x in grant.values()):
            raise ControlError("CONFIG_INVALID", "Grant values must be printable strings.")
        parsed.append(Grant(**grant))
    try:
        enrollment = Enrollment(**{k: v for k, v in value.items() if k not in {"schema_version", "grants"}}, grants=tuple(parsed))
        enrollment.validate()
        if str(UUID(enrollment.mailbox_epoch)) != enrollment.mailbox_epoch:
            raise ControlError("CONFIG_INVALID", "Enrollment epoch must use canonical UUID spelling.")
        return enrollment
    except (TypeError, ValueError) as exc:
        raise ControlError("CONFIG_INVALID", "Local enrollment is invalid; review the example configuration.") from exc


def default_target_state(request: Mapping[str, Any], enrollment: Enrollment) -> Mapping[str, str]:
    if request["capability"] != "system.ping":
        raise ControlError("TARGET_UNAVAILABLE", "This target requires its enrolled provider; no fallback target is selected.")
    return {"device": enrollment.target_device, "scope": "device"}


@dataclass(frozen=True, slots=True)
class Preview:
    request_id: str
    digest: str
    capability: str
    target_device: str
    scope: str
    destination_repository_id: int
    destination_branch: str
    mailbox_epoch: str
    expires_at: str
    effect: str
    policy_revision: int
    target_state_sha256: str
    enrollment_sha256: str
    state: str


EFFECTS = {
    "system.ping": "Return a bounded nonce; no host secrets.",
    "terminal.tail": "Read only the enrolled session; export requires sanitization.",
    "window.list": "List only enrolled windows; titles require sanitization.",
    "capture.window": "Capture the exact enrolled window; keep image local for review.",
}


class ControlService:
    """One authority path for local imports and trusted transport ingestion.

    The loader is called again for approval and dispatch so policy edits cannot
    silently inherit a previous approval, even when a revision was not bumped.
    """
    def __init__(self, journal: Journal, enrollment_loader: Callable[[], Enrollment], *,
                 target_state: Callable[[Mapping[str, Any], Enrollment], Mapping[str, str]] = default_target_state,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.journal = journal
        self.enrollment_loader = enrollment_loader
        self.target_state = target_state
        self.clock = clock

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ControlError("CLOCK_INVALID", "The local clock must be timezone-aware.")
        return value.astimezone(timezone.utc)

    def _context(self, request: Mapping[str, Any]) -> tuple[Enrollment, str, dict[str, str]]:
        enrollment = self.enrollment_loader()
        enrollment.validate()
        fingerprint = canonical_sha256(asdict(enrollment))
        state = dict(self.target_state(request, enrollment))
        if len(state) > 32 or not all(isinstance(k, str) and isinstance(v, str) and len(k) <= 64 and len(v) <= 512 for k, v in state.items()):
            raise ControlError("TARGET_INVALID", "Provider target identity must be bounded local metadata.")
        state["enrollment_sha256"] = fingerprint
        return enrollment, fingerprint, state

    @staticmethod
    def _origin(enrollment: Enrollment) -> TransportOrigin:
        return TransportOrigin(enrollment.repository_id, enrollment.inbox_branch, enrollment.mailbox_epoch)

    def admit(self, data: bytes, *, origin: TransportOrigin | None = None) -> tuple[RequestKey, str]:
        now = self._now()
        request = parse_request(data, now=now)
        enrollment, fingerprint, target = self._context(request)
        actual_origin = origin or self._origin(enrollment)
        decision = evaluate(request, enrollment=enrollment, origin=actual_origin, target_state=target, now=now)
        binding = {"enrollment_sha256": fingerprint, "origin": asdict(actual_origin),
                   "target_state_sha256": decision.target_state_sha256,
                   "source": "transport" if origin else "local_import", "initial_action": decision.action}
        return self.journal.receive(actual_origin.repository_id, request, decision, now=now, binding=binding)

    def key(self, request_id: str) -> RequestKey:
        try:
            identifier = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ControlError("REQUEST_ID_INVALID", "Use a canonical request UUID.") from exc
        enrollment = self.enrollment_loader()
        return RequestKey(enrollment.repository_id, enrollment.mailbox_epoch, identifier)

    def _decision(self, key: RequestKey, *, approval: Approval | None = None) -> tuple[Decision, str, Enrollment]:
        row = self.journal.get(key)
        binding = self.journal.authority(key)["binding"]
        enrollment, fingerprint, target = self._context(row["request"])
        if binding["enrollment_sha256"] != fingerprint:
            raise ControlError("AUTHORITY_CHANGED", "Enrollment changed; deny this request and use a fresh request ID.")
        origin = TransportOrigin(**binding["origin"])
        decision = evaluate(row["request"], enrollment=enrollment, origin=origin, target_state=target,
                            approval=approval, now=self._now())
        if decision.target_state_sha256 != binding["target_state_sha256"]:
            raise ControlError("TARGET_CHANGED", "The target changed; use a fresh request and preview.")
        return decision, fingerprint, enrollment

    def preview(self, key: RequestKey) -> Preview:
        row = self.journal.get(key)
        decision, fingerprint, enrollment = self._decision(key)
        return Preview(key.request_id, row["input_sha256"], row["capability"], row["request"]["target_device"],
                       request_scope(row["request"]), enrollment.repository_id, enrollment.outbox_branch,
                       enrollment.mailbox_epoch, row["expires_at"], EFFECTS[row["capability"]],
                       decision.policy_revision, decision.target_state_sha256, fingerprint, row["state"])

    def approve(self, key: RequestKey, *, preview: Preview, typed_digest: str, lifetime_seconds: int = 120) -> str:
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 300:
            raise ControlError("APPROVAL_LIMIT", "Approval lifetime must be 1..300 seconds.")
        if typed_digest != preview.digest:
            raise ControlError("CONFIRMATION_MISMATCH", "The typed digest did not match the reviewed request.")
        # Prompt happens BEFORE this short transaction. Recompute everything now.
        with self.journal.transaction():
            current = self.preview(key)
            if current != preview or current.state != "awaiting_approval":
                raise ControlError("PREVIEW_STALE", "Request or local state changed since preview.")
            expires = min(datetime.fromisoformat(current.expires_at.replace("Z", "+00:00")),
                          self._now() + timedelta(seconds=lifetime_seconds))
            approval = Approval(current.digest, current.policy_revision, current.target_state_sha256, expires)
            decision, _, _ = self._decision(key, approval=approval)
            self.journal.approve(key, decision, now=self._now())
            encoded = asdict(approval)
            encoded["expires_at"] = expires.isoformat()
            self.journal.store_approval(key, encoded)
        return "ready"

    def authorize(self, key: RequestKey) -> Decision:
        saved = self.journal.authority(key)["approval"]
        approval = None
        if saved is not None:
            approval = Approval(**{**saved, "expires_at": datetime.fromisoformat(saved["expires_at"])})
        decision, _, _ = self._decision(key, approval=approval)
        return decision

    def dispatch(self, key: RequestKey, registry: CapabilityRegistry) -> str:
        self.journal.expire_due(now=self._now())
        return Dispatcher(self.journal, registry, authorize=self.authorize).execute(key, now=self._now())

    def deny(self, key: RequestKey) -> str:
        return self.journal.deny(key, now=self._now())

    def revoke(self, key: RequestKey) -> str:
        return self.journal.deny(key, code="APPROVAL_REVOKED", now=self._now())
