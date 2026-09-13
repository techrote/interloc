from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("reconcile", ROOT / "tools/workflow/reconcile.py")
reconcile = importlib.util.module_from_spec(spec); spec.loader.exec_module(reconcile)


def body(stable_id):
    headings = ["Objective", "Scope and non-goals", "Dependencies and concurrency", "Context", "Implementation prompt", "Acceptance criteria", "Verification", "Expected artifacts", "Blocking and stopping conditions"]
    return f"<!-- interloc:{stable_id} -->\n" + "\n".join("## " + value for value in headings)


def plan():
    return {
        "milestones": [{"id":"M0","title":"Foundation"}],
        "tasks": [
            {"id":"IL-001","issue":2,"requires":[],"publication":"published","blockers":[],"required_gates":[],"verification":"docs/evidence/IL-001.md"},
            {"id":"IL-002","issue":3,"requires":["IL-001"],"publication":"published","blockers":[],"required_gates":[],"verification":"docs/evidence/IL-002.md"},
            {"id":"IL-003","issue":4,"requires":["IL-002"],"publication":"published","blockers":[],"required_gates":[],"verification":"docs/evidence/IL-003.md"},
            {"id":"IL-004","issue":5,"requires":["IL-002"],"publication":"published","blockers":[],"required_gates":["G-positive"],"verification":"docs/evidence/IL-004.md"},
        ],
    }


def issue(n, stable_id, state="open", reason=None, extra=""):
    return {"number":n,"title":f"[{stable_id}] title","body":body(stable_id)+extra,"state":state,"state_reason":reason}


class ReconcileTests(unittest.TestCase):
    def test_clean_snapshot_has_no_issue_mutations(self):
        p=plan(); issues=[issue(2,"IL-001","closed","completed"), issue(3,"IL-002"), issue(4,"IL-003"), issue(5,"IL-004")]
        result=reconcile.reconciliation_plan(p,issues,{"docs/evidence/IL-001.md"})
        self.assertEqual(result["errors"],[]); self.assertEqual(result["issue_mutations"],[]); self.assertEqual(result["completed"],["IL-001"]); self.assertEqual(result["ready"],["IL-002"])

    def test_second_dry_run_is_identical(self):
        p=plan(); issues=[issue(2,"IL-001"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004")]
        a=reconcile.reconciliation_plan(p,issues,set()); b=reconcile.reconciliation_plan(p,issues,set())
        self.assertEqual(a,b)

    def test_closed_without_evidence_is_not_completed(self):
        p=plan(); issues=[issue(2,"IL-001","closed","completed"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004")]
        self.assertEqual(reconcile.completed_from_snapshot(p,issues,set()),set())

    def test_duplicate_live_marker_fails(self):
        p=plan(); issues=[issue(2,"IL-001"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004"),issue(9,"IL-002")]
        self.assertTrue(any("multiple non-duplicate" in x for x in reconcile.audit_snapshot(p,issues)["errors"]))

    def test_closed_duplicate_tombstone_is_preserved_not_canonicalized(self):
        p=plan(); issues=[issue(2,"IL-001"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004"),issue(9,"IL-002","closed","duplicate",extra="\nHuman finding remains")]
        result=reconcile.audit_snapshot(p,issues)
        self.assertEqual(result["errors"],[]); self.assertTrue(any("tombstones" in x for x in result["notes"]))

    def test_canonical_issue_marked_duplicate_fails(self):
        p=plan(); issues=[issue(2,"IL-001","closed","duplicate"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004")]
        self.assertTrue(any("canonical issue #2 is marked duplicate" in x for x in reconcile.audit_snapshot(p,issues)["errors"]))

    def test_missing_sections_fail_without_body_rewrite_plan(self):
        p=plan(); issues=[issue(2,"IL-001"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004")]
        issues[1]["body"]="<!-- interloc:IL-002 -->\n## Objective"
        result=reconcile.reconciliation_plan(p,issues,set())
        self.assertTrue(result["errors"]); self.assertEqual(result["issue_mutations"],[])

    def test_gate_is_not_inferred_from_dependency_completion(self):
        p=plan(); completed={"IL-001","IL-002"}
        self.assertNotIn("IL-004",reconcile.ready_from_snapshot(p,completed))
        self.assertIn("IL-004",reconcile.ready_from_snapshot(p,completed,{"G-positive"}))

    def test_blocked_task_and_dependents_never_become_ready_from_closure(self):
        p=plan(); p["tasks"][1]["publication"]="blocked_publication"; p["tasks"][1]["blockers"]=["B"]
        self.assertNotIn("IL-003",reconcile.ready_from_snapshot(p,{"IL-001","IL-002"}))

    def test_metadata_plan_is_create_only_and_idempotent(self):
        p=plan(); first=reconcile.metadata_plan(p,{"milestones":[],"labels":[]})
        self.assertTrue(first); self.assertTrue(all(x["operation"].startswith("create_") for x in first))
        desired=reconcile.desired_metadata(p)
        self.assertEqual(reconcile.metadata_plan(p,desired),[])

    def test_human_body_additions_do_not_trigger_mutation(self):
        p=plan(); issues=[issue(2,"IL-001",extra="\nHuman finding: keep me"),issue(3,"IL-002"),issue(4,"IL-003"),issue(5,"IL-004")]
        result=reconcile.reconciliation_plan(p,issues,set())
        self.assertEqual(result["errors"],[]); self.assertEqual(result["issue_mutations"],[])


if __name__ == "__main__": unittest.main()
