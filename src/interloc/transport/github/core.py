"""Private GitHub mailbox transport using fixed gh.exe operations.

The transport is deliberately narrower than a generic GitHub client: it verifies one
locally enrolled private repository, reads immutable request JSON from one inbox
branch, and serializes text publication to one per-device outbox branch.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import datetime, timedelta, timezone
import json
import os
import random
import re
import subprocess
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urlencode

from interloc.protocol import ProtocolError, safe_mailbox_path

MAX_REQUEST_BYTES = 32 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_GROUP_BYTES = 256 * 1024
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    headers: Mapping[str, str]
    data: Any


class Api(Protocol):
    def request(self, method: str, endpoint: str, *, body: Mapping[str, Any] | None = None) -> ApiResponse: ...


def _clean_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    denied = {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}
    result = {key: value for key, value in source.items() if key.upper() not in denied}
    result["GH_HOST"] = "github.com"
    result["GH_PROMPT_DISABLED"] = "1"
    return result


def _split_included_output(raw: str) -> tuple[int, dict[str, str], str]:
    normalized = raw.replace("\r\n", "\n")
    blocks = normalized.split("\n\n", 1)
    if len(blocks) != 2 or not blocks[0].startswith("HTTP/"):
        return 200, {}, raw
    head, body = blocks
    lines = head.splitlines()
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        status = 200
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().casefold()] = value.strip()
    return status, headers, body


class GhCliApi:
    """Fixed-host gh api adapter; caller cannot supply gh argv or headers."""
    def __init__(self, executable: str = "gh", runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self.executable = executable
        self.runner = runner

    def request(self, method: str, endpoint: str, *, body: Mapping[str, Any] | None = None) -> ApiResponse:
        if method not in {"GET", "POST", "PATCH"}:
            raise TransportError("METHOD_DENIED", "only GET/POST/PATCH are supported")
        if not endpoint.startswith("/") or "\n" in endpoint or "\r" in endpoint:
            raise TransportError("ENDPOINT_INVALID", "endpoint must be a fixed github.com REST path")
        argv = [self.executable, "api", "--hostname", "github.com", "--include", "-X", method, endpoint,
                "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
        payload: str | None = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            argv.extend(["--input", "-"])
        try:
            completed = self.runner(argv, input=payload, text=True, capture_output=True, env=_clean_env(), shell=False, check=False, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise TransportError("GH_UNAVAILABLE", type(exc).__name__) from exc
        status, headers, body_text = _split_included_output(completed.stdout or "")
        if completed.returncode != 0 and status == 200:
            match = re.search(r"HTTP\s+(\d{3})", (completed.stderr or ""), flags=re.I)
            status = int(match.group(1)) if match else 599
        retry_after = None
        if "retry-after" in headers:
            try:
                retry_after = max(0.0, float(headers["retry-after"]))
            except ValueError:
                pass
        data: Any = None
        if body_text.strip():
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                data = body_text
        response = ApiResponse(status, headers, data)
        if status >= 400 or completed.returncode != 0:
            if status in {401, 403}:
                code = "AUTH_OR_PERMISSION"
            elif status == 404:
                code = "NOT_FOUND"
            elif status in {409, 422}:
                code = "REF_CONFLICT"
            elif status == 429:
                code = "RATE_LIMITED"
            elif status >= 500:
                code = "REMOTE_TRANSIENT"
            else:
                code = "REMOTE_ERROR"
            raise TransportError(code, f"GitHub request failed with HTTP {status}", status=status, retry_after=retry_after)
        return response


@dataclass(frozen=True, slots=True)
class MailboxEnrollment:
    repository_id: int
    repository_full_name: str
    inbox_branch: str
    outbox_branch: str
    device_id: str

    def validate(self) -> None:
        if type(self.repository_id) is not int or self.repository_id <= 0:
            raise TransportError("CONFIG_INVALID", "repository_id must be positive")
        if not FULL_NAME_RE.fullmatch(self.repository_full_name):
            raise TransportError("CONFIG_INVALID", "repository_full_name is invalid")
        for name, value in (("inbox_branch", self.inbox_branch), ("outbox_branch", self.outbox_branch)):
            if not SAFE_BRANCH_RE.fullmatch(value) or ".." in value or value.endswith("/") or "@{" in value:
                raise TransportError("CONFIG_INVALID", f"{name} is unsafe")
        if self.inbox_branch == self.outbox_branch:
            raise TransportError("CONFIG_INVALID", "inbox and outbox must be separate")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.device_id):
            raise TransportError("CONFIG_INVALID", "device_id is invalid")


@dataclass(frozen=True, slots=True)
class RemoteRequest:
    path: str
    commit_sha: str
    blob_sha: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Publication:
    commit_sha: str
    manifest_path: str
    reused: bool


def _safe_path(value: str) -> str:
    try:
        return safe_mailbox_path(value)
    except ProtocolError as exc:
        raise TransportError("PATH_DENIED", "mailbox path is unsafe") from exc


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise TransportError("REMOTE_MALFORMED", f"{field} is not a full lowercase SHA")
    return value


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_content(data: Mapping[str, Any], *, max_bytes: int) -> tuple[str, bytes]:
    blob = _require_sha(data.get("sha"), "blob sha")
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise TransportError("REMOTE_MALFORMED", "contents response is not base64")
    try:
        raw = base64.b64decode(data["content"], validate=False)
    except Exception as exc:
        raise TransportError("REMOTE_MALFORMED", "invalid base64 content") from exc
    if len(raw) > max_bytes:
        raise TransportError("REMOTE_TOO_LARGE", f"remote object exceeds {max_bytes} bytes")
    return blob, raw


class PollPlanner:
    def __init__(self, *, active_seconds: float = 30.0, idle_seconds: float = 120.0, rng: random.Random | None = None) -> None:
        self.active_seconds = active_seconds
        self.idle_seconds = idle_seconds
        self.rng = rng or random.Random()

    def delay(self, *, active: bool, failures: int = 0, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return max(retry_after, 1.0)
        base = self.active_seconds if active else self.idle_seconds
        factor = min(2 ** max(0, failures), 16)
        jitter = self.rng.uniform(0.9, 1.1)
        return min(base * factor * jitter, 3600.0)


class AttemptBudget:
    def __init__(self, max_attempts: int = 120, window: timedelta = timedelta(hours=1), clock: Callable[[], datetime] | None = None) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._times: list[datetime] = []

    def consume(self) -> None:
        now = self.clock().astimezone(timezone.utc)
        cutoff = now - self.window
        self._times = [value for value in self._times if value > cutoff]
        if len(self._times) >= self.max_attempts:
            raise TransportError("LOCAL_RATE_BUDGET", "poll attempt budget exhausted")
        self._times.append(now)


class GitHubMailbox:
    def __init__(self, api: Api, enrollment: MailboxEnrollment, *, max_pages: int = 20) -> None:
        enrollment.validate()
        if not (1 <= max_pages <= 100):
            raise TransportError("CONFIG_INVALID", "max_pages must be 1..100")
        self.api = api
        self.enrollment = enrollment
        self.max_pages = max_pages
        self.owner, self.repo = enrollment.repository_full_name.split("/", 1)

    def _ep(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    def verify_enrollment(self) -> None:
        repo = self.api.request("GET", f"/repositories/{self.enrollment.repository_id}").data
        if not isinstance(repo, Mapping):
            raise TransportError("REMOTE_MALFORMED", "repository response is not an object")
        if repo.get("id") != self.enrollment.repository_id or repo.get("full_name") != self.enrollment.repository_full_name:
            raise TransportError("REPOSITORY_MISMATCH", "numeric repository identity/full name changed")
        if repo.get("private") is not True:
            raise TransportError("REPOSITORY_PUBLIC", "runtime mailbox must remain private")
        self._ref(self.enrollment.inbox_branch)
        self._ref(self.enrollment.outbox_branch)

    def _ref(self, branch: str) -> str:
        encoded = quote(branch, safe="")
        data = self.api.request("GET", self._ep(f"/git/ref/heads/{encoded}")).data
        if not isinstance(data, Mapping) or not isinstance(data.get("object"), Mapping):
            raise TransportError("REMOTE_MALFORMED", "ref response is invalid")
        return _require_sha(data["object"].get("sha"), "ref sha")

    def _contents_at(self, path: str, commit_sha: str, *, max_bytes: int) -> tuple[str, bytes] | None:
        safe = _safe_path(path)
        endpoint = self._ep(f"/contents/{quote(safe, safe='/')}?{urlencode({'ref': commit_sha})}")
        try:
            response = self.api.request("GET", endpoint)
        except TransportError as exc:
            if exc.status == 404 or exc.code == "NOT_FOUND":
                return None
            raise
        if not isinstance(response.data, Mapping):
            raise TransportError("REMOTE_MALFORMED", "contents response is invalid")
        return _decode_content(response.data, max_bytes=max_bytes)

    def read_text_at(self, path: str, commit_sha: str, *, max_bytes: int = MAX_MANIFEST_BYTES) -> bytes:
        _require_sha(commit_sha, "commit sha")
        result = self._contents_at(path, commit_sha, max_bytes=max_bytes)
        if result is None:
            raise TransportError("NOT_FOUND", "path does not exist at immutable ref", status=404)
        return result[1]

    def _request_paths_from_commit(self, sha: str) -> list[str]:
        prefix = f"requests/{self.enrollment.device_id}/"
        paths: list[str] = []
        for page in range(1, self.max_pages + 1):
            endpoint = self._ep(f"/commits/{sha}?{urlencode({'per_page': 100, 'page': page})}")
            data = self.api.request("GET", endpoint).data
            if not isinstance(data, Mapping) or not isinstance(data.get("files"), list):
                raise TransportError("REMOTE_MALFORMED", "commit response lacks files")
            files = data["files"]
            for item in files:
                if not isinstance(item, Mapping):
                    continue
                path = item.get("filename")
                status = item.get("status")
                if isinstance(path, str) and path.startswith(prefix) and path.endswith(".json") and status != "removed":
                    paths.append(_safe_path(path))
            if len(files) < 100:
                break
        else:
            raise TransportError("PAGINATION_LIMIT", "commit file pagination exceeded configured limit")
        return paths

    def _full_tree_paths(self, head: str) -> list[tuple[str, str]]:
        commit = self.api.request("GET", self._ep(f"/git/commits/{head}")).data
        if not isinstance(commit, Mapping) or not isinstance(commit.get("tree"), Mapping):
            raise TransportError("REMOTE_MALFORMED", "commit tree missing")
        tree_sha = _require_sha(commit["tree"].get("sha"), "tree sha")
        tree = self.api.request("GET", self._ep(f"/git/trees/{tree_sha}?recursive=1")).data
        if not isinstance(tree, Mapping) or not isinstance(tree.get("tree"), list):
            raise TransportError("REMOTE_MALFORMED", "tree response invalid")
        if tree.get("truncated") is True:
            raise TransportError("TREE_TRUNCATED", "recursive tree was truncated; refusing incomplete discovery")
        prefix = f"requests/{self.enrollment.device_id}/"
        result = []
        for item in tree["tree"]:
            if not isinstance(item, Mapping):
                continue
            path = item.get("path")
            if item.get("type") == "blob" and isinstance(path, str) and path.startswith(prefix) and path.endswith(".json"):
                result.append((_safe_path(path), _require_sha(item.get("sha"), "blob sha")))
        return result

    def discover_requests(self, last_seen_sha: str | None) -> tuple[str, tuple[RemoteRequest, ...]]:
        head = self._ref(self.enrollment.inbox_branch)
        if last_seen_sha == head:
            return head, ()
        if last_seen_sha is not None:
            _require_sha(last_seen_sha, "last_seen_sha")
        commits: list[str] = []
        found = last_seen_sha is None
        if last_seen_sha is not None:
            for page in range(1, self.max_pages + 1):
                endpoint = self._ep(f"/commits?{urlencode({'sha': self.enrollment.inbox_branch, 'per_page': 100, 'page': page})}")
                data = self.api.request("GET", endpoint).data
                if not isinstance(data, list):
                    raise TransportError("REMOTE_MALFORMED", "commit listing is not an array")
                for item in data:
                    if not isinstance(item, Mapping):
                        continue
                    sha = _require_sha(item.get("sha"), "commit sha")
                    if sha == last_seen_sha:
                        found = True
                        break
                    commits.append(sha)
                if found or len(data) < 100:
                    break
        objects: list[RemoteRequest] = []
        if last_seen_sha is None or not found:
            for path, blob_sha in self._full_tree_paths(head):
                got = self._contents_at(path, head, max_bytes=MAX_REQUEST_BYTES)
                if got is None:
                    continue
                returned_blob, raw = got
                if returned_blob != blob_sha:
                    raise TransportError("BLOB_MISMATCH", "tree and contents blob identities differ")
                objects.append(RemoteRequest(path, head, blob_sha, raw))
            return head, tuple(objects)
        for commit_sha in reversed(commits):
            for path in self._request_paths_from_commit(commit_sha):
                got = self._contents_at(path, commit_sha, max_bytes=MAX_REQUEST_BYTES)
                if got is None:
                    continue
                blob, raw = got
                objects.append(RemoteRequest(path, commit_sha, blob, raw))
        return head, tuple(objects)

    def publish_group(self, *, group_id: str, files: Mapping[str, bytes], manifest_path: str, manifest_bytes: bytes, retries: int = 3) -> Publication:
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", group_id):
            raise TransportError("GROUP_INVALID", "group_id is invalid")
        manifest_path = _safe_path(manifest_path)
        if not manifest_path.startswith("indexes/") or not manifest_path.endswith(".json"):
            raise TransportError("PATH_DENIED", "manifest must be a generated indexes/*.json path")
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise TransportError("REMOTE_TOO_LARGE", "manifest exceeds 64 KiB")
        total = len(manifest_bytes)
        normalized: dict[str, bytes] = {}
        for path, data in files.items():
            safe = _safe_path(path)
            if not safe.startswith(("sessions/", "responses/")):
                raise TransportError("PATH_DENIED", "outbox data path has unsupported prefix")
            if not isinstance(data, bytes):
                raise TransportError("DATA_INVALID", "published data must be bytes")
            total += len(data)
            normalized[safe] = data
        if total > MAX_GROUP_BYTES:
            raise TransportError("REMOTE_TOO_LARGE", "publication group exceeds 256 KiB")
        for attempt in range(retries):
            head = self._ref(self.enrollment.outbox_branch)
            existing = self._contents_at(manifest_path, head, max_bytes=MAX_MANIFEST_BYTES)
            if existing is not None:
                if existing[1] == manifest_bytes:
                    return Publication(head, manifest_path, True)
                raise TransportError("PUBLICATION_COLLISION", "manifest path exists with different bytes")
            commit = self.api.request("GET", self._ep(f"/git/commits/{head}")).data
            if not isinstance(commit, Mapping) or not isinstance(commit.get("tree"), Mapping):
                raise TransportError("REMOTE_MALFORMED", "outbox head commit lacks tree")
            base_tree = _require_sha(commit["tree"].get("sha"), "base tree sha")
            tree_entries = []
            for path, data in [*normalized.items(), (manifest_path, manifest_bytes)]:
                blob = self.api.request("POST", self._ep("/git/blobs"), body={"content": _b64(data), "encoding": "base64"}).data
                if not isinstance(blob, Mapping):
                    raise TransportError("REMOTE_MALFORMED", "blob response invalid")
                tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": _require_sha(blob.get("sha"), "created blob sha")})
            tree = self.api.request("POST", self._ep("/git/trees"), body={"base_tree": base_tree, "tree": tree_entries}).data
            if not isinstance(tree, Mapping):
                raise TransportError("REMOTE_MALFORMED", "created tree response invalid")
            tree_sha = _require_sha(tree.get("sha"), "created tree sha")
            created = self.api.request("POST", self._ep("/git/commits"), body={"message": f"interloc: publish {group_id}", "tree": tree_sha, "parents": [head]}).data
            if not isinstance(created, Mapping):
                raise TransportError("REMOTE_MALFORMED", "created commit response invalid")
            commit_sha = _require_sha(created.get("sha"), "created commit sha")
            try:
                self.api.request("PATCH", self._ep(f"/git/refs/heads/{quote(self.enrollment.outbox_branch, safe='')}"), body={"sha": commit_sha, "force": False})
                return Publication(commit_sha, manifest_path, False)
            except TransportError as exc:
                if exc.code != "REF_CONFLICT" or attempt + 1 >= retries:
                    raise
        raise TransportError("REF_CONFLICT", "publication retries exhausted")
