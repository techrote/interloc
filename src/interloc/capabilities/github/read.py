"""Explicitly enrolled read-only GitHub/worktree capabilities. No runtime wiring."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlencode

from interloc.evidence import EvidenceGroup, StreamingSanitizer, build_text_group
from interloc.policy import Approval, Enrollment, TransportOrigin, evaluate
from interloc.protocol import ProtocolError, canonical_sha256, parse_request, strict_loads, validate_request
from interloc.protocol.reads import ALIAS, READ_CAPABILITIES, SHA
from interloc.transport.github.core import ApiResponse
from .process import ReadError, environment, run_bounded
from .worktree import Worktree, inspect_worktree

GITHUB_READS = READ_CAPABILITIES - {"git.worktree.status"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
ENDPOINT = re.compile(r"/repos/[A-Za-z0-9][A-Za-z0-9_-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}(?:/(?:issues(?:/[1-9][0-9]{0,9})?|pulls/[1-9][0-9]{0,9}|commits/[0-9a-f]{40}/check-runs))?(?:\?(?:state=(?:open|closed|all)&)?page=[1-9][0-9]{0,3}&per_page=[1-9][0-9]{0,2})?\Z")


@dataclass(frozen=True)
class Project:
    alias: str
    repository_id: int
    full_name: str
    capabilities: tuple[str, ...]
    private: bool = True

    def validate(self) -> None:
        if not isinstance(self.alias, str) or not ALIAS.fullmatch(self.alias) or not isinstance(self.full_name, str) or not NAME.fullmatch(self.full_name):
            raise ReadError("ENROLLMENT_INVALID")
        if type(self.repository_id) is not int or not 1 <= self.repository_id < 2**63 or type(self.private) is not bool:
            raise ReadError("ENROLLMENT_INVALID")
        if not isinstance(self.capabilities, tuple) or not self.capabilities or not set(self.capabilities) <= GITHUB_READS or len(set(self.capabilities)) != len(self.capabilities):
            raise ReadError("ENROLLMENT_INVALID")


class GhReadApi:
    """Only fixed GET endpoints; opaque remote headers/URLs never become argv."""
    def __init__(self, executable: Path, *, runner=run_bounded) -> None:
        self.executable, self.runner = executable, runner
        self._lock = threading.Lock()
        self._attempts: list[float] = []
        self._not_before = 0.0

    def get(self, endpoint: str, *, cancelled: Callable[[], bool] = lambda: False) -> ApiResponse:
        if not isinstance(endpoint, str) or not ENDPOINT.fullmatch(endpoint):
            raise ReadError("ENDPOINT_DENIED")
        with self._lock:
            now = time.monotonic()
            self._attempts = [t for t in self._attempts if t > now - 3600]
            if now < self._not_before:
                raise ReadError("RATE_LIMITED")
            if len(self._attempts) >= 120:
                raise ReadError("LOCAL_RATE_BUDGET")
            self._attempts.append(now)
        env = environment(broker=True)
        env.update({"GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1"})
        with tempfile.TemporaryDirectory(prefix="interloc-gh-read-") as temp:
            completed = self.runner([str(self.executable), "api", "--hostname", "github.com", "--include", "--method", "GET",
                                     "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint],
                                    cwd=Path(temp), env=env, timeout=15, max_stdout=1048576, cancelled=cancelled)
        raw = completed.stdout.replace(b"\r\n", b"\n")
        header, separator, body = raw.partition(b"\n\n")
        if not separator or len(header) > 16384:
            raise ReadError("AUTH_UNAVAILABLE" if completed.returncode == 4 else "REMOTE_UNAVAILABLE")
        lines = header.split(b"\n")
        match = re.fullmatch(rb"HTTP/[0-9.]+ ([0-9]{3})(?: [^\r\n]*)?", lines[0])
        if not match:
            raise ReadError("REMOTE_MALFORMED")
        status = int(match.group(1))
        headers = {}
        for line in lines[1:]:
            name, colon, value = line.partition(b":")
            if not colon:
                raise ReadError("REMOTE_MALFORMED")
            key = name.decode("ascii", "strict").lower()
            if key in {"retry-after", "x-ratelimit-remaining", "x-ratelimit-reset"}:
                if key in headers:
                    raise ReadError("REMOTE_MALFORMED")
                headers[key] = value.strip().decode("ascii", "strict")
        delay = 0.0
        try:
            if "retry-after" in headers:
                value = headers["retry-after"]
                delay = float(int(value)) if value.isdigit() else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            if headers.get("x-ratelimit-remaining") == "0":
                delay = max(delay, int(headers.get("x-ratelimit-reset", "0")) - time.time())
        except (ValueError, TypeError, OverflowError) as exc:
            raise ReadError("REMOTE_MALFORMED") from exc
        if delay > 0 or status in {403, 429}:
            with self._lock:
                self._not_before = max(self._not_before, time.monotonic() + max(delay, 60))
        if status != 200 or completed.returncode:
            if status == 304:
                code = "NOT_MODIFIED"
            elif status == 401 or completed.returncode == 4:
                code = "AUTH_UNAVAILABLE"
            elif status == 403:
                code = "RATE_LIMITED" if delay > 0 or headers.get("x-ratelimit-remaining") == "0" else "PERMISSION_DENIED"
            elif status == 404:
                code = "NOT_FOUND_OR_HIDDEN"
            elif status == 429:
                code = "RATE_LIMITED"
            else:
                code = "REMOTE_UNAVAILABLE"
            raise ReadError(code)
        try:
            data = strict_loads(body, max_bytes=1048576)
        except (ProtocolError, ValueError, RecursionError, UnicodeError) as exc:
            raise ReadError("REMOTE_MALFORMED") from exc
        return ApiResponse(status, headers, data)


def _integer(value, *, maximum=2**63 - 1) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise ReadError("REMOTE_MALFORMED")
    return value


def _text(value, *, maximum=65536, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ReadError("REMOTE_MALFORMED")
    return value


def _issue(value, project: Project, *, number=None, pr=False) -> dict:
    if not isinstance(value, dict):
        raise ReadError("REMOTE_MALFORMED")
    actual = _integer(value.get("number"), maximum=2147483647)
    if number is not None and actual != number:
        raise ReadError("REMOTE_IDENTITY_CHANGED")
    if value.get("url") != f"https://api.github.com/repos/{project.full_name}/{'pulls' if pr else 'issues'}/{actual}":
        raise ReadError("REMOTE_IDENTITY_CHANGED")
    state = value.get("state")
    if not isinstance(state, str) or state not in {"open", "closed"}:
        raise ReadError("REMOTE_MALFORMED")
    result = {"id": _integer(value.get("id")), "number": actual, "state": state,
              "title": _text(value.get("title"), maximum=1024), "body": _text(value.get("body"), nullable=True),
              "updated_at": _text(value.get("updated_at"), maximum=40)}
    if pr:
        head = value.get("head")
        if not isinstance(head, dict) or not isinstance(head.get("sha"), str) or not SHA.fullmatch(head["sha"]):
            raise ReadError("REMOTE_MALFORMED")
        result["head_sha"] = head["sha"]
    elif "pull_request" in value:
        raise ReadError("NOT_AN_ISSUE")
    return result


@dataclass(frozen=True)
class ReadResult:
    code: str
    evidence: EvidenceGroup


class ReadAdapters:
    """No default registration. Mailbox grants AND separate project mappings apply."""
    def __init__(self, *, enrollment_loader: Callable[[], Enrollment], projects: tuple[Project, ...] = (),
                 worktrees: tuple[Worktree, ...] = (), enabled: bool = False, api: GhReadApi | None = None,
                 git_executable: Path | None = None, secrets: tuple[str, ...] = (), personal_paths: tuple[str, ...] = (),
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        if type(enabled) is not bool or len(projects) > 64 or len(worktrees) > 64:
            raise ReadError("ENROLLMENT_INVALID")
        for project in projects:
            project.validate()
        if len({p.alias for p in projects}) != len(projects) or len({t.alias for t in worktrees}) != len(worktrees):
            raise ReadError("ENROLLMENT_INVALID")
        self.projects, self.worktrees = tuple(projects), tuple(worktrees)
        self.loader, self.enabled, self.api, self.git = enrollment_loader, enabled, api, git_executable
        self.secrets, self.paths, self.clock = tuple(secrets), tuple(personal_paths), clock

    def target_state(self, request: dict) -> dict[str, str]:
        cap, args = request["capability"], request["arguments"]
        if cap == "git.worktree.status":
            targets = [t for t in self.worktrees if t.alias == args["worktree_alias"]]
            if not targets:
                raise ReadError("WORKTREE_NOT_ENROLLED")
            target = {"alias": targets[0].alias, "root": str(targets[0].root), "git_dir": str(targets[0].git_dir), "common_dir": str(targets[0].common_dir)}
        else:
            targets = [p for p in self.projects if p.alias == args["repo_alias"] and cap in p.capabilities]
            if not targets:
                raise ReadError("PROJECT_NOT_ENROLLED")
            target = asdict(targets[0])
        return {"read_target_sha256": canonical_sha256(target), "mailbox_sha256": canonical_sha256(asdict(self.loader()))}

    def observe(self, request: bytes | dict, *, origin: TransportOrigin, approval: Approval | None = None,
                cancelled: Callable[[], bool] = lambda: False) -> ReadResult:
        expiry_deadline = None
        try:
            if not self.enabled:
                raise ReadError("EXTENSION_DISABLED")
            normalized = parse_request(request, now=self.clock()) if isinstance(request, bytes) else validate_request(request, now=self.clock())
            if normalized["capability"] not in READ_CAPABILITIES:
                raise ReadError("CAPABILITY_DENIED")
            if cancelled():
                raise ReadError("CANCELLED")
            expires = datetime.fromisoformat(normalized["expires_at"].replace("Z", "+00:00"))
            expiry_deadline = time.monotonic() + max(0, (expires - self.clock()).total_seconds())
            def guard():
                if not self.enabled:
                    raise ReadError("EXTENSION_DISABLED")
                if self.clock() >= expires or time.monotonic() >= expiry_deadline:
                    raise ReadError("EXPIRED")
                if cancelled():
                    raise ReadError("CANCELLED")
            def stopped():
                return cancelled() or not self.enabled or self.clock() >= expires or time.monotonic() >= expiry_deadline
            guard()
            target_state = self.target_state(normalized)
            enrollment = self.loader()
            decision = evaluate(normalized, enrollment=enrollment, origin=origin, target_state=target_state, approval=approval, now=self.clock())
            if not decision.permitted:
                raise ReadError("APPROVAL_REQUIRED" if decision.action == "confirm" else "POLICY_DENIED")
            cap, args = normalized["capability"], normalized["arguments"]
            if cap == "git.worktree.status":
                if self.git is None:
                    raise ReadError("EXECUTABLE_UNAVAILABLE")
                tree = next(t for t in self.worktrees if t.alias == args["worktree_alias"])
                value = inspect_worktree(tree, self.git, cancelled=stopped)
            else:
                value = self._github(cap, args, stopped, guard)
            guard()
            if self.target_state(normalized) != target_state:
                raise ReadError("AUTHORITY_CHANGED")
            if not evaluate(normalized, enrollment=self.loader(), origin=origin, target_state=target_state, approval=approval, now=self.clock()).permitted:
                raise ReadError("AUTHORITY_CHANGED")
            return self._result("OK", value, normalized["capability"])
        except ReadError as exc:
            code = exc.code
            if code == "CANCELLED" and expiry_deadline is not None:
                if self.clock() >= expires or time.monotonic() >= expiry_deadline:
                    code = "EXPIRED"
                elif not self.enabled:
                    code = "EXTENSION_DISABLED"
            return self._result(code, None)
        except (ProtocolError, ValueError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
            return self._result("EXPIRED" if getattr(exc, "code", None) == "EXPIRED" else "INVALID_REQUEST_OR_RESPONSE", None)

    def _github(self, capability: str, args: dict, cancelled, guard) -> dict:
        if self.api is None:
            raise ReadError("AUTH_UNAVAILABLE")
        project = next(p for p in self.projects if p.alias == args["repo_alias"])
        root = f"/repos/{project.full_name}"
        def get(endpoint):
            guard()
            response = self.api.get(endpoint, cancelled=cancelled)
            if response.status != 200:
                raise ReadError("REMOTE_UNAVAILABLE")
            return response.data
        def verify():
            meta = get(root)
            if not isinstance(meta, dict) or type(meta.get("id")) is not int or meta["id"] != project.repository_id or meta.get("full_name") != project.full_name or meta.get("private") is not project.private:
                raise ReadError("REMOTE_IDENTITY_CHANGED")
        verify()
        result = {"repo_alias": project.alias, "repository_id": project.repository_id}
        if capability in {"github.issue.read", "github.pr.read"}:
            pr = capability == "github.pr.read"
            result["item"] = _issue(get(f"{root}/{'pulls' if pr else 'issues'}/{args['number']}"), project, number=args["number"], pr=pr)
        else:
            query = {"state": args["state"]} if capability == "github.issues.list" else {}
            query.update(page=args["page"], per_page=args["per_page"])
            suffix = "/issues" if capability == "github.issues.list" else f"/commits/{args['commit_sha']}/check-runs"
            data = get(root + suffix + "?" + urlencode(query))
            rows = data if capability == "github.issues.list" else data.get("check_runs") if isinstance(data, dict) else None
            if not isinstance(rows, list) or len(rows) > args["per_page"]:
                raise ReadError("REMOTE_MALFORMED")
            items = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ReadError("REMOTE_MALFORMED")
                if capability == "github.issues.list":
                    if "pull_request" not in row:
                        items.append(_issue(row, project))
                else:
                    identifier = _integer(row.get("id"))
                    if row.get("head_sha") != args["commit_sha"] or row.get("url") != f"https://api.github.com/repos/{project.full_name}/check-runs/{identifier}":
                        raise ReadError("REMOTE_IDENTITY_CHANGED")
                    status = row.get("status")
                    if not isinstance(status, str) or status not in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}:
                        raise ReadError("REMOTE_MALFORMED")
                    items.append({"id": identifier, "name": _text(row.get("name"), maximum=1024), "status": status,
                                  "conclusion": _text(row.get("conclusion"), maximum=64, nullable=True)})
            result.update(items=items, page=args["page"], possibly_more=len(rows) == args["per_page"], skipped_pull_requests=len(rows) - len(items))
            if capability == "github.checks.read":
                result.update(commit_sha=args["commit_sha"], qualification="reported-check-runs-only-not-a-merge-decision")
        # A rename/visibility/identity change discards the whole observation.
        verify()
        return result

    def _result(self, code: str, data, capability: str | None = None) -> ReadResult:
        # Redact string values BEFORE JSON encoding: escape spelling must not hide
        # a configured path/secret. A second pass protects cross-fragment tokens.
        paths = self.paths + tuple(str(p) for t in self.worktrees for p in (t.root, t.git_dir, t.common_dir))
        def clean(value):
            if isinstance(value, str):
                sanitizer = StreamingSanitizer(secrets=self.secrets, personal_paths=paths)
                return sanitizer.feed(value) + sanitizer.finish()
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            return value
        payload = json.dumps(clean({"code": code, "capability": capability, "data": data}), ensure_ascii=False, sort_keys=True)
        group = build_text_group([payload], secrets=self.secrets, personal_paths=paths, max_group_bytes=262144)
        return ReadResult(code, group)
