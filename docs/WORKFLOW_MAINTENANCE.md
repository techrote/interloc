# Workflow maintenance

`tools/audit_workflow.py` remains the repository/file/DAG structural auditor. `tools/workflow/reconcile.py` adds a deliberately read-only reconciliation layer for a complete GitHub issue snapshot.

The reconciler never contacts GitHub, edits issue bodies, closes issues, creates labels/milestones or executes `gh`. It treats the stable `<!-- interloc:IL-xxx -->` marker plus `docs/workflow.json` issue number as canonical identity. Closed issues whose `state_reason` is `duplicate` are preserved as historical tombstones and cannot satisfy a dependency.

A task counts as completed for scheduling only when the canonical issue is `closed` with `state_reason=completed` **and** its declared `docs/evidence/IL-xxx.md` file exists in the checked-out tree. Required gates remain independent: issue closure cannot synthesize a positive gate.

## Offline reconciliation

Obtain a complete issue snapshot through an authorized GitHub client and save it locally as JSON. The tool accepts GitHub REST-style `number` or connector-style `issue_number` fields. Run each command separately:

```text
python tools/audit_workflow.py
python tools/workflow/reconcile.py --issues path/to/issues.json
```

A clean rerun over identical inputs is deterministic and reports an empty `issue_mutations` list. Human additions to otherwise valid canonical issue bodies are not overwritten.

## Optional native metadata

If you also provide a JSON object with `milestones: []` and `labels: []`, pass it using `--metadata`. The reconciler emits **create-only dry-run operations** for missing canonical milestone titles and the small programme label set. It never deletes, renames or overwrites native metadata.

```text
python tools/workflow/reconcile.py --issues path/to/issues.json --metadata path/to/metadata.json
```

Materializing those operations is optional and requires a separately authorized local GitHub workflow with exact target review. This repository intentionally does not turn the planning auditor into a network credential holder.

## Fail-closed cases

Reconciliation reports errors for missing canonical issues, identity mismatch, a canonical issue marked duplicate, multiple non-duplicate issues carrying the same stable marker, or missing execution sections. Resolve ambiguity manually; do not auto-create/rewrite issues to make the audit green.
