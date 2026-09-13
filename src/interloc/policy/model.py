"""Local authority model for Interlocuator.

Remote protocol bytes describe a request. This module decides whether that request
is eligible to run; requester-supplied attribution never becomes authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Mapping
from uuid import UUID

from interloc.protocol import ProtocolError, request_digest, validate_request

from interloc.protocol.reads import READ_CAPABILITIES

CORE_CAPABILITIES = {"system.ping", "terminal.tail", "window.list", "capture.window"}
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SAFE_REF_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.?(/|$))[A-Za-z0-9._/-]{1,200}$")
DECISIONS = {"allow", "confirm", "deny"}


class PolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _uuid(text: str, *, field_name: str) -> str:
    try:
        return str(UUID(text))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PolicyError("CONFIG_INVALID", f"{field_name} must be a UUID") from exc


def _safe_ref(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not SAFE_REF_RE.fullmatch(value) or "//" in value or value.endswith("/"):
        raise PolicyError("CONFIG_INVALID", f"{field_name} is not a safe Git ref name")
    if value.endswith(".lock") or "@{" in value or ".." in value:
        raise PolicyError("CONFIG_INVALID", f"{field_name} contains forbidden Git-ref syntax")
    return value


def _state_digest(parts: Mapping[str, str]) -> str:
    data = json.dumps(dict(sorted(parts.items())), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class Grant:
    capability: str
    scope_id: str
    decision: str
    destination: str = "mailbox"

    def validate(self) -> None:
        if self.capability not in CORE_CAPABILITIES | READ_CAPABILITIES:
            raise PolicyError("CONFIG_INVALID", f"unsupported capability grant {self.capability!r}")
        if not isinstance(self.scope_id, str) or not self.scope_id or len(self.scope_id) > 128 or self.scope_id == "*":
            raise PolicyError("CONFIG_INVALID", "scope_id must be explicit and bounded; wildcard is forbidden")
        if self.decision not in DECISIONS:
            raise PolicyError("CONFIG_INVALID", "decision must be allow, confirm or deny")
        if self.destination != "mailbox":
            raise PolicyError("CONFIG_INVALID", "only the enrolled mailbox destination is supported")
        if self.capability == "capture.window" and self.decision == "allow":
            raise PolicyError("CONFIG_INVALID", "capture.window cannot be globally auto-allowed")


@dataclass(frozen=True, slots=True)
class Enrollment:
    repository_id: int
    mailbox_epoch: str
    target_device: str
    private_repository: bool
    inbox_branch: str
    outbox_branch: str
    grants: tuple[Grant, ...] = field(default_factory=tuple)
    revision: int = 1

    def validate(self) -> None:
        if type(self.repository_id) is not int or self.repository_id <= 0:
            raise PolicyError("CONFIG_INVALID", "repository_id must be a positive integer")
        _uuid(self.mailbox_epoch, field_name="mailbox_epoch")
        if not isinstance(self.target_device, str) or not SAFE_ID_RE.fullmatch(self.target_device):
            raise PolicyError("CONFIG_INVALID", "target_device is invalid")
        if self.private_repository is not True:
            raise PolicyError("CONFIG_INVALID", "runtime mailbox must be private")
        _safe_ref(self.inbox_branch, field_name="inbox_branch")
        _safe_ref(self.outbox_branch, field_name="outbox_branch")
        if self.inbox_branch == self.outbox_branch:
            raise PolicyError("CONFIG_INVALID", "inbox and outbox branches must differ")
        if type(self.revision) is not int or self.revision < 1:
            raise PolicyError("CONFIG_INVALID", "revision must be a positive integer")
        seen: set[tuple[str, str, str]] = set()
        for grant in self.grants:
            grant.validate()
            key = (grant.capability, grant.scope_id, grant.destination)
            if key in seen:
                raise PolicyError("CONFIG_INVALID", f"duplicate grant for {grant.capability}/{grant.scope_id}")
            seen.add(key)


@dataclass(frozen=True, slots=True)
class TransportOrigin:
    repository_id: int
    branch: str
    mailbox_epoch: str


@dataclass(frozen=True, slots=True)
class Approval:
    request_sha256: str
    policy_revision: int
    target_state_sha256: str
    expires_at: datetime
    revoked: bool = False

    def valid_for(self, *, request_sha256: str, policy_revision: int, target_state_sha256: str, now: datetime) -> bool:
        return (
            not self.revoked
            and self.request_sha256 == request_sha256
            and self.policy_revision == policy_revision
            and self.target_state_sha256 == target_state_sha256
            and self.expires_at.tzinfo is not None
            and self.expires_at.astimezone(timezone.utc) > now.astimezone(timezone.utc)
        )


@dataclass(frozen=True, slots=True)
class Decision:
    action: str
    code: str
    reason: str
    request_sha256: str
    policy_revision: int
    target_state_sha256: str

    @property
    def permitted(self) -> bool:
        return self.action == "allow"


def request_scope(request: Mapping[str, object]) -> str:
    capability = request["capability"]
    if capability == "system.ping":
        return "device"
    if capability == "terminal.tail":
        return "session:" + str(request["session_id"])
    arguments = request["arguments"]
    assert isinstance(arguments, Mapping)
    if capability in READ_CAPABILITIES:
        key = "worktree_alias" if capability == "git.worktree.status" else "repo_alias"
        prefix = "worktree:" if key == "worktree_alias" else "project:"
        return prefix + str(arguments[key])
    if capability in {"window.list", "capture.window"}:
        return "capture:" + str(arguments["capture_scope_id"])
    raise PolicyError("UNKNOWN_CAPABILITY", f"unsupported capability {capability!r}")


def evaluate(
    request: Mapping[str, object],
    *,
    enrollment: Enrollment,
    origin: TransportOrigin,
    target_state: Mapping[str, str],
    approval: Approval | None = None,
    destination: str = "mailbox",
    now: datetime | None = None,
) -> Decision:
    """Return a pure deny/confirm/allow decision.

    `requester_label` is deliberately ignored for authority. Origin identity comes
    from transport metadata and local enrollment only.
    """
    enrollment.validate()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        normalized = validate_request(dict(request), now=current)
    except ProtocolError as exc:
        raise PolicyError("REQUEST_INVALID", str(exc)) from exc
    digest = request_digest(normalized)
    state_digest = _state_digest(target_state)

    def decision(action: str, code: str, reason: str) -> Decision:
        return Decision(action, code, reason, digest, enrollment.revision, state_digest)

    if origin.repository_id != enrollment.repository_id:
        return decision("deny", "ORIGIN_REPOSITORY_MISMATCH", "request did not originate from the enrolled mailbox repository")
    if origin.mailbox_epoch != enrollment.mailbox_epoch or normalized["mailbox_epoch"] != enrollment.mailbox_epoch:
        return decision("deny", "EPOCH_MISMATCH", "mailbox epoch does not match local enrollment")
    if origin.branch != enrollment.inbox_branch:
        return decision("deny", "ORIGIN_BRANCH_MISMATCH", "request did not originate from the enrolled inbox branch")
    if normalized["target_device"] != enrollment.target_device:
        return decision("deny", "DEVICE_MISMATCH", "request targets a different device")
    if destination != "mailbox":
        return decision("deny", "DESTINATION_DENIED", "destination is not the enrolled mailbox")

    scope = request_scope(normalized)
    grants = [g for g in enrollment.grants if g.capability == normalized["capability"] and g.scope_id == scope and g.destination == destination]
    if not grants:
        return decision("deny", "NO_GRANT", "no exact local grant exists for this capability and scope")
    grant = grants[0]
    if grant.decision == "deny":
        return decision("deny", "LOCAL_DENY", "local policy explicitly denies this operation")
    if grant.decision == "confirm":
        if approval is None:
            return decision("confirm", "APPROVAL_REQUIRED", "explicit local approval is required")
        if not approval.valid_for(request_sha256=digest, policy_revision=enrollment.revision, target_state_sha256=state_digest, now=current):
            return decision("confirm", "APPROVAL_STALE", "approval is revoked, expired, or bound to different request/policy/target state")
    return decision("allow", "ALLOWED", "exact local grant and all authority bindings are valid")


def resolve_scoped_path(root: Path, relative: str) -> Path:
    """Resolve an explicitly configured relative path and reject current symlink escapes.

    Runtime file users must still reopen/revalidate at use time to handle races.
    """
    if not isinstance(relative, str) or not relative:
        raise PolicyError("PATH_INVALID", "relative path is empty")
    text = relative.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", relative) or text.startswith("//") or ":" in relative:
        raise PolicyError("PATH_INVALID", "absolute, UNC, drive or ADS path syntax is forbidden")
    parts = PurePosixPath(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise PolicyError("PATH_INVALID", "dot/traversal components are forbidden")
    base = root.resolve(strict=True)
    candidate = base.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise PolicyError("PATH_ESCAPE", "resolved path escapes enrolled root") from exc
    return resolved
