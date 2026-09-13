"""Fixed IL-016 argument contracts. Recognition is not local authorization."""
from __future__ import annotations
import re
from typing import Any
from .errors import ProtocolError

READ_CAPABILITIES = frozenset({"github.issue.read", "github.issues.list", "github.pr.read", "github.checks.read", "git.worktree.status"})
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def read_arguments(capability: str, value: Any) -> dict[str, Any]:
    if capability not in READ_CAPABILITIES or not isinstance(value, dict):
        raise ProtocolError("READ_ARGUMENTS_INVALID", "unsupported read operation or argument shape")
    alias_key = "worktree_alias" if capability == "git.worktree.status" else "repo_alias"
    required, optional = {alias_key}, set()
    if capability in {"github.issue.read", "github.pr.read"}:
        required.add("number")
    if capability == "github.checks.read":
        required.add("commit_sha")
    if capability in {"github.issues.list", "github.checks.read"}:
        optional |= {"page", "per_page"}
    if capability == "github.issues.list":
        optional.add("state")
    if set(value) - required - optional or required - set(value):
        raise ProtocolError("READ_ARGUMENTS_INVALID", "missing or unknown read argument")
    if not isinstance(value[alias_key], str) or not ALIAS.fullmatch(value[alias_key]):
        raise ProtocolError("READ_ALIAS_INVALID", "expected an enrolled opaque alias")
    result = dict(value)
    for key, default, maximum in (("number", None, 2147483647), ("page", 1, 1000), ("per_page", 20, 100)):
        if key not in required | optional:
            continue
        number = value.get(key, default)
        if type(number) is not int or not 1 <= number <= maximum:
            raise ProtocolError("READ_LIMIT_INVALID", "numeric read argument is outside its bound")
        result[key] = number
    if "state" in optional:
        state = value.get("state", "open")
        if not isinstance(state, str) or state not in {"open", "closed", "all"}:
            raise ProtocolError("READ_ARGUMENTS_INVALID", "invalid issue state filter")
        result["state"] = state
    if "commit_sha" in required and (not isinstance(value["commit_sha"], str) or not SHA.fullmatch(value["commit_sha"])):
        raise ProtocolError("READ_REF_INVALID", "checks require a full lowercase commit SHA")
    return result
