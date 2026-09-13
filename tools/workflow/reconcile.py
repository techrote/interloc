"""Deterministic, read-only reconciliation of Interloc workflow snapshots.

This module never calls GitHub and never mutates repository state. It consumes a
complete issue snapshot (and, optionally, native-label/milestone snapshots) and
returns review/apply plans for an authorized operator or a future fixed gh wrapper.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

MARKER_RE = re.compile(r"<!--\s*interloc:(IL-\d{3})\s*-->")
REQUIRED_SECTIONS = {
    "objective", "scope and non-goals", "dependencies and concurrency", "context",
    "implementation prompt", "acceptance criteria", "verification",
    "expected artifacts", "blocking and stopping conditions",
}


def number_of(issue: dict[str, Any]) -> int:
    value = issue.get("number", issue.get("issue_number"))
    if type(value) is not int or value < 1:
        raise ValueError("issue snapshot entry lacks a positive number")
    return value


def marker_of(issue: dict[str, Any]) -> str | None:
    matches = MARKER_RE.findall(issue.get("body") or "")
    if len(matches) > 1:
        raise ValueError(f"issue #{number_of(issue)} contains multiple Interloc markers")
    return matches[0] if matches else None


def is_duplicate_tombstone(issue: dict[str, Any]) -> bool:
    return issue.get("state") == "closed" and issue.get("state_reason") == "duplicate"


def _headings(body: str) -> set[str]:
    return {value.strip().casefold() for value in re.findall(r"^## (.+)$", body, flags=re.M)}


def canonical_map(plan: dict[str, Any]) -> dict[str, int]:
    return {task["id"]: task["issue"] for task in plan["tasks"]}


def audit_snapshot(plan: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Audit canonical identity without erasing historical duplicate tombstones."""
    errors: list[str] = []
    notes: list[str] = []
    by_number: dict[int, dict[str, Any]] = {}
    by_marker: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        try:
            number = number_of(issue)
            marker = marker_of(issue)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if number in by_number:
            errors.append(f"duplicate snapshot entry for issue #{number}")
        by_number[number] = issue
        if marker:
            by_marker.setdefault(marker, []).append(issue)

    for stable_id, expected_number in canonical_map(plan).items():
        issue = by_number.get(expected_number)
        if issue is None:
            errors.append(f"{stable_id}: canonical issue #{expected_number} missing from snapshot")
            continue
        marker = marker_of(issue)
        if marker != stable_id or f"[{stable_id}]" not in (issue.get("title") or ""):
            errors.append(f"{stable_id}: canonical issue #{expected_number} identity mismatch")
        if is_duplicate_tombstone(issue):
            errors.append(f"{stable_id}: canonical issue #{expected_number} is marked duplicate")
        missing = REQUIRED_SECTIONS - _headings(issue.get("body") or "")
        if missing:
            errors.append(f"{stable_id}: canonical issue #{expected_number} missing sections {sorted(missing)}")

    for stable_id, grouped in sorted(by_marker.items()):
        live = [issue for issue in grouped if not is_duplicate_tombstone(issue)]
        tombstones = [number_of(issue) for issue in grouped if is_duplicate_tombstone(issue)]
        if len(live) > 1:
            errors.append(f"{stable_id}: multiple non-duplicate issues {[number_of(x) for x in live]}")
        if tombstones:
            notes.append(f"{stable_id}: preserved duplicate tombstones {sorted(tombstones)}")
    return {"errors": errors, "notes": notes}


def completed_from_snapshot(plan: dict[str, Any], issues: list[dict[str, Any]], inventory: set[str]) -> set[str]:
    by_number = {number_of(issue): issue for issue in issues}
    completed: set[str] = set()
    for task in plan["tasks"]:
        issue = by_number.get(task["issue"])
        if not issue or marker_of(issue) != task["id"]:
            continue
        if issue.get("state") == "closed" and issue.get("state_reason") == "completed" and task["verification"] in inventory:
            completed.add(task["id"])
    return completed


def ancestors(tasks: dict[str, dict[str, Any]], key: str) -> set[str]:
    result: set[str] = set()
    todo = list(tasks[key]["requires"])
    while todo:
        value = todo.pop()
        if value in result:
            continue
        result.add(value)
        if value in tasks:
            todo.extend(tasks[value]["requires"])
    return result


def ready_from_snapshot(plan: dict[str, Any], completed: set[str], gates: set[str] | None = None) -> list[str]:
    tasks = {task["id"]: task for task in plan["tasks"]}
    gates = gates or set()
    unavailable = {key for key, task in tasks.items() if task["publication"] != "published" or task.get("blockers")}
    ready = []
    for key, task in tasks.items():
        if key in completed or key in unavailable or ancestors(tasks, key) & unavailable:
            continue
        if set(task["requires"]) <= completed and set(task.get("required_gates", [])) <= gates:
            ready.append(key)
    return sorted(ready)


def desired_metadata(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return optional native metadata; repository docs remain authoritative."""
    milestones = [{"title": item["title"], "description": f"Interloc {item['id']}: {item['title']}"} for item in plan["milestones"]]
    labels = [
        {"name": "interloc", "description": "Interlocuator programme work"},
        {"name": "optional", "description": "Not required for supervised MVP"},
        {"name": "conditional", "description": "Requires an explicit positive gate"},
    ]
    return {"milestones": milestones, "labels": labels}


def metadata_plan(plan: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Produce fixed create-only metadata operations; never deletes or overwrites."""
    desired = desired_metadata(plan)
    current_milestones = {item.get("title") for item in snapshot.get("milestones", [])}
    current_labels = {item.get("name") for item in snapshot.get("labels", [])}
    operations: list[dict[str, Any]] = []
    for item in desired["milestones"]:
        if item["title"] not in current_milestones:
            operations.append({"operation": "create_milestone", **item})
    for item in desired["labels"]:
        if item["name"] not in current_labels:
            operations.append({"operation": "create_label", **item})
    return operations


def reconciliation_plan(plan: dict[str, Any], issues: list[dict[str, Any]], inventory: set[str], metadata_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    audit = audit_snapshot(plan, issues)
    completed = completed_from_snapshot(plan, issues, inventory)
    result: dict[str, Any] = {
        "errors": audit["errors"],
        "notes": audit["notes"],
        "completed": sorted(completed),
        "ready": ready_from_snapshot(plan, completed),
        "issue_mutations": [],
    }
    if metadata_snapshot is not None:
        result["optional_metadata_operations"] = metadata_plan(plan, metadata_snapshot)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--issues", type=Path, required=True, help="complete JSON issue snapshot")
    parser.add_argument("--metadata", type=Path, help="optional JSON snapshot with milestones[] and labels[]")
    args = parser.parse_args()
    try:
        plan = json.loads((args.root / "docs/workflow.json").read_text(encoding="utf-8"))
        issues = json.loads(args.issues.read_text(encoding="utf-8"))
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None
        inventory = {p.relative_to(args.root).as_posix() for p in args.root.rglob("*") if p.is_file()}
        result = reconciliation_plan(plan, issues, inventory, metadata)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result["errors"] else 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
