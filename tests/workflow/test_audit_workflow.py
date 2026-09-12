"""Offline mutation tests for the planning auditor, not Interloc runtime tests."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('audit', ROOT / 'tools/audit_workflow.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
PLAN = json.loads((ROOT / 'docs/workflow.json').read_text())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.plan = copy.deepcopy(PLAN)

    def test_valid_graph(self):
        self.assertEqual(audit.validate(self.plan), [])

    def test_duplicate_id(self):
        self.plan['tasks'].append(copy.deepcopy(self.plan['tasks'][0]))
        self.assertIn('duplicate task ID', audit.validate(self.plan))

    def test_duplicate_issue(self):
        self.plan['tasks'][1]['issue'] = 2
        self.assertIn('invalid or duplicate issue number', audit.validate(self.plan))

    def test_cycle(self):
        self.plan['tasks'][0]['requires'] = ['IL-002']
        self.assertTrue(any('cycle' in x for x in audit.validate(self.plan)))

    def test_missing_dependency(self):
        self.plan['tasks'][0]['requires'] = ['IL-999']
        self.assertTrue(any('missing dependency' in x for x in audit.validate(self.plan)))

    def test_missing_verification(self):
        self.plan['tasks'][0]['verification'] = ''
        self.assertTrue(any('verification' in x for x in audit.validate(self.plan)))

    def test_missing_document(self):
        self.assertTrue(any('canonical document' in x for x in audit.validate(self.plan, set())))

    def test_unsafe_path(self):
        self.plan['tasks'][0]['paths'] = ['../outside']
        self.assertTrue(any('unsafe owned path' in x for x in audit.validate(self.plan)))

    def test_missing_issue_without_blocker(self):
        self.plan['tasks'][0]['issue'] = None
        self.assertTrue(any('missing issue' in x for x in audit.validate(self.plan)))

    def test_initial_readiness(self):
        self.assertEqual(audit.ready(self.plan, set()), ['IL-001'])

    def test_post_foundation_readiness(self):
        self.assertEqual(audit.ready(self.plan, {'IL-001'}), ['IL-002', 'IL-020', 'IL-028'])

    def test_conditional_transport_requires_positive_gate(self):
        completed = {'IL-001', 'IL-002', 'IL-003', 'IL-004', 'IL-005', 'IL-006', 'IL-007', 'IL-008',
                     'IL-009', 'IL-010', 'IL-011', 'IL-020', 'IL-021'}
        self.assertNotIn('IL-022', audit.ready(self.plan, completed))
        self.assertIn('IL-022', audit.ready(self.plan, completed, {'G-assisted'}))

    def test_independent_hardening_not_blocked_by_visual(self):
        self.assertIn('IL-025', audit.ready(self.plan, {'IL-010', 'IL-021', 'IL-024'}))

    def test_gate_not_implied_by_dependencies(self):
        self.plan['tasks'][1]['required_gates'] = ['example-positive-gate']
        self.assertNotIn('IL-002', audit.ready(self.plan, {'IL-001'}))

    def test_dependency_conflict(self):
        self.assertTrue(audit.conflicts(self.plan, 'IL-001', 'IL-002'))

    def test_lock_conflict(self):
        self.assertTrue(audit.conflicts(self.plan, 'IL-012', 'IL-023'))

    def test_casefold_path_conflict(self):
        self.plan['tasks'][4]['paths'] = ['Shared/']
        self.plan['tasks'][5]['paths'] = ['shared/file.py']
        self.assertTrue(audit.conflicts(self.plan, 'IL-005', 'IL-006'))

    def test_independent_lanes(self):
        self.assertEqual(audit.conflicts(self.plan, 'IL-025', 'IL-026'), [])

    def test_link_validation(self):
        self.assertEqual(audit.check_links('docs/A.md', '[root](../README.md)', {'README.md'}), [])
        self.assertTrue(audit.check_links('docs/A.md', '[bad](missing.md)', {'README.md'}))

    def test_issue_execution_sections(self):
        task = self.plan['tasks'][0]
        one = copy.deepcopy(self.plan)
        one['tasks'] = [task]
        item = {'number': 2, 'title': '[IL-001] Test', 'body': '<!-- interloc:IL-001 -->\n' +
                '\n'.join('## ' + h for h in audit.SECTIONS)}
        self.assertEqual(audit.check_issues(one, [item]), [])
        item['body'] = item['body'].replace('## verification', '')
        self.assertTrue(audit.check_issues(one, [item]))


if __name__ == '__main__':
    unittest.main()
