"""Read-only Interloc planning checks. No network, credentials or write operations."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

STATUSES = {'published', 'blocked_tracker', 'blocked_publication', 'deferred_unpublished'}
SECTIONS = ('objective', 'scope and non-goals', 'dependencies and concurrency', 'context',
            'implementation prompt', 'acceptance criteria', 'verification',
            'expected artifacts', 'blocking and stopping conditions')


def safe_path(value: str) -> bool:
    p = PurePosixPath(value)
    return bool(value) and not p.is_absolute() and '..' not in p.parts and not any(c in value for c in ':\\')


def ancestors(tasks: dict[str, dict[str, Any]], key: str) -> set[str]:
    seen: set[str] = set()
    todo = list(tasks[key]['requires'])
    while todo:
        dep = todo.pop()
        if dep in seen:
            continue
        seen.add(dep)
        if dep in tasks:
            todo.extend(tasks[dep]['requires'])
    return seen


def validate(plan: dict[str, Any], inventory: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if plan.get('schema_version') != 1 or plan.get('repository') != 'techrote/interloc':
        errors.append('unexpected schema or repository')
    tasks = plan.get('tasks', [])
    ids = [t['id'] for t in tasks]
    if len(set(ids)) != len(ids):
        errors.append('duplicate task ID')
    mapping = {t['id']: t for t in tasks}
    numbers = [plan['overview_issue']] + [t['issue'] for t in tasks if t['issue'] is not None]
    if len(set(numbers)) != len(numbers) or any(type(n) is not int or n < 1 for n in numbers):
        errors.append('invalid or duplicate issue number')
    milestones = {m['id'] for m in plan['milestones']}
    blockers = {b['id'] for b in plan['blockers']}
    for t in tasks:
        key = t['id']
        if not re.fullmatch(r'IL-\d{3}', key) or t['milestone'] not in milestones:
            errors.append(f'{key}: invalid ID or milestone')
        if t['publication'] not in STATUSES:
            errors.append(f'{key}: invalid publication state')
        if t['publication'] == 'published' and (t['issue'] is None or t['blockers']):
            errors.append(f'{key}: published task missing issue or has explicit blocker')
        if t['publication'] != 'published' and not t['blockers']:
            errors.append(f'{key}: unpublished task lacks blocker')
        if t['publication'] == 'blocked_tracker' and t['issue'] is None:
            errors.append(f'{key}: missing blocker tracker')
        if any(b not in blockers for b in t['blockers']):
            errors.append(f'{key}: unknown blocker')
        if len(set(t['requires'])) != len(t['requires']) or any(d not in mapping for d in t['requires']):
            errors.append(f'{key}: duplicate or missing dependency')
        if key in ancestors(mapping, key):
            errors.append(f'{key}: dependency cycle')
        if not t.get('verification') or not safe_path(t['verification']):
            errors.append(f'{key}: missing or unsafe verification path')
        if t['publication'] == 'published' and not t['paths']:
            errors.append(f'{key}: missing path ownership')
        if any(not safe_path(p) for p in t['paths']):
            errors.append(f'{key}: unsafe owned path')
    for path in plan['canonical_docs']:
        if not safe_path(path) or (inventory is not None and path not in inventory):
            errors.append(f'missing or unsafe canonical document: {path}')
    return errors


def ready(plan: dict[str, Any], completed: set[str], gates: set[str] | None = None) -> list[str]:
    tasks = {t['id']: t for t in plan['tasks']}
    gates = gates or set()
    blocked = {k for k, t in tasks.items() if t['publication'] != 'published' or t['blockers']}
    return sorted(k for k, t in tasks.items() if k not in completed and k not in blocked
                  and not (ancestors(tasks, k) & blocked)
                  and set(t['requires']) <= completed and set(t['required_gates']) <= gates)


def conflicts(plan: dict[str, Any], a: str, b: str) -> list[str]:
    tasks = {t['id']: t for t in plan['tasks']}
    reasons = []
    if a == b or a in ancestors(tasks, b) or b in ancestors(tasks, a):
        reasons.append('same task or dependency ordering')
    reasons += ['lock: ' + x for x in sorted(set(tasks[a]['locks']) & set(tasks[b]['locks']))]
    for left in tasks[a]['paths']:
        for right in tasks[b]['paths']:
            x, y = left.rstrip('/').casefold(), right.rstrip('/').casefold()
            if x == y or x.startswith(y + '/') or y.startswith(x + '/'):
                reasons.append('overlapping paths: ' + left + ' / ' + right)
    return reasons


def check_links(path: str, text: str, inventory: set[str]) -> list[str]:
    errors = []
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    prefix = 'https://github.com/techrote/interloc/blob/main/'
    for link in re.findall(r'\[[^\]]*\]\(([^)\s]+)\)', text):
        raw = unquote(link.split('#', 1)[0])
        if not raw:
            continue
        if raw.startswith(prefix):
            target = raw[len(prefix):]
        elif urlsplit(raw).scheme or raw.startswith('//'):
            continue
        else:
            parts = list(PurePosixPath(path).parent.parts)
            for part in PurePosixPath(raw).parts:
                if part == '..' and parts:
                    parts.pop()
                elif part == '..':
                    errors.append(f'{path}: escaping link {link}')
                elif part != '.':
                    parts.append(part)
            target = '/'.join(parts)
        if target not in inventory:
            errors.append(f'{path}: missing link target {target}')
    return errors


def check_issues(plan: dict[str, Any], issues: list[dict[str, Any]]) -> list[str]:
    errors = []
    by_number = {i['number']: i for i in issues if 'pull_request' not in i}
    if len(by_number) != len([i for i in issues if 'pull_request' not in i]):
        errors.append('duplicate issue snapshot number')
    for t in plan['tasks']:
        if t['issue'] is None:
            continue
        issue = by_number.get(t['issue'])
        if issue is None:
            errors.append(f"{t['id']}: missing issue snapshot")
            continue
        body = issue.get('body') or ''
        if body.count('<!-- interloc:' + t['id'] + ' -->') != 1 or '[' + t['id'] + ']' not in issue['title']:
            errors.append(f"{t['id']}: identity mismatch")
        headings = {h.strip().casefold() for h in re.findall(r'^## (.+)$', body, flags=re.M)}
        if set(SECTIONS) - headings:
            errors.append(f"{t['id']}: missing execution sections")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--graph-only', action='store_true', help='Do not check files or Markdown links')
    parser.add_argument('--issues', type=Path, help='Optional complete REST-style issue snapshot JSON array')
    args = parser.parse_args()
    try:
        plan = json.loads((args.root / 'docs/workflow.json').read_text(encoding='utf-8'))
        inventory = {p.relative_to(args.root).as_posix() for p in args.root.rglob('*') if p.is_file()}
        errors = validate(plan, None if args.graph_only else inventory)
        if not args.graph_only:
            for path in plan['canonical_docs']:
                if path in inventory:
                    errors += check_links(path, (args.root / path).read_text(encoding='utf-8'), inventory)
        if args.issues:
            errors += check_issues(plan, json.loads(args.issues.read_text(encoding='utf-8')))
        print(json.dumps({'errors': errors, 'task_count': len(plan['tasks']),
                          'ready_without_completed_prerequisites': ready(plan, set()),
                          'issue_snapshot_checked': bool(args.issues),
                          'document_links_checked': not args.graph_only}, indent=2))
        return 1 if errors else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
