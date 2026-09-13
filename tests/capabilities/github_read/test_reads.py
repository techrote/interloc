from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from interloc.capabilities.github.process import ProcessResult, ReadError
from interloc.capabilities.github.read import GhReadApi, Project, ReadAdapters
from interloc.evidence import text_group_export
from interloc.policy import Approval, Enrollment, Grant, TransportOrigin, evaluate
from interloc.protocol import ProtocolError, canonical_sha256, parse_request, validate_request
from interloc.protocol.reads import READ_CAPABILITIES, read_arguments
from interloc.transport.github.core import ApiResponse

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
EPOCH = '00000000-0000-4000-8000-000000000001'
SHA = 'a' * 40
PROJECT = Project('project', 123, 'test/project', tuple(sorted(READ_CAPABILITIES - {'git.worktree.status'})))
META = {'id': 123, 'full_name': 'test/project', 'private': True}
ORIGIN = TransportOrigin(42, 'inbox', EPOCH)


def request(cap='github.issue.read', **arguments):
    if not arguments:
        arguments = {'repo_alias': 'project', 'number': 7}
    return dict(schema_version=1, request_id=str(uuid4()), mailbox_epoch=EPOCH, target_device='fixture', requester_label='untrusted',
                created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=5)).isoformat(), capability=cap, arguments=arguments)


def item(number=7, *, pr=False):
    return dict(id=100 + number, number=number, title='synthetic', body='synthetic body', state='open', updated_at=NOW.isoformat(),
                url=f'https://api.github.com/repos/test/project/{"pulls" if pr else "issues"}/{number}', head={'sha': SHA})


def enrollment(*, decision='allow', grant=True):
    grants = tuple(Grant(cap, 'worktree:tree' if cap == 'git.worktree.status' else 'project:project', decision) for cap in READ_CAPABILITIES) if grant else ()
    return Enrollment(42, EPOCH, 'fixture', True, 'inbox', 'outbox/fixture', grants=grants)


def payload(result):
    return json.loads(''.join(c.text for c in result.evidence.chunks))


class FakeApi:
    def __init__(self, values=None):
        self.calls = []
        self.values = values or [dict(META), item(), dict(META)]
    def get(self, endpoint, *, cancelled):
        if cancelled():
            raise ReadError('CANCELLED')
        self.calls.append(endpoint)
        value = self.values.pop(0)
        if callable(value):
            value = value()
        if isinstance(value, Exception):
            raise value
        return ApiResponse(200, {}, value)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.mailbox = enrollment()
        self.api = FakeApi()
        self.adapter = ReadAdapters(enrollment_loader=lambda: self.mailbox, projects=(PROJECT,), enabled=True, api=self.api, clock=lambda: NOW)
    def observe(self, value=None, **kwargs):
        return self.adapter.observe(request() if value is None else value, origin=kwargs.pop('origin', ORIGIN), **kwargs)

    def test_disabled_by_default_has_no_io(self):
        adapter = ReadAdapters(enrollment_loader=enrollment, projects=(PROJECT,), api=self.api, clock=lambda: NOW)
        self.assertEqual(adapter.observe(request(), origin=ORIGIN).code, 'EXTENSION_DISABLED')
        self.assertEqual(self.api.calls, [])

    def test_project_mapping_does_not_replace_mailbox_policy(self):
        self.mailbox = enrollment(grant=False)
        self.assertEqual(self.observe().code, 'POLICY_DENIED')
        self.assertEqual(self.api.calls, [])

    def test_mailbox_permission_does_not_enroll_project(self):
        self.adapter.projects = ()
        self.assertEqual(self.observe().code, 'PROJECT_NOT_ENROLLED')
        self.assertEqual(self.api.calls, [])

    def test_project_capability_allowlist_is_exact(self):
        self.adapter.projects = (replace(PROJECT, capabilities=('github.pr.read',)),)
        self.assertEqual(self.observe().code, 'PROJECT_NOT_ENROLLED')
        self.assertEqual(self.api.calls, [])

    def test_forged_label_wrong_origin_never_authorizes(self):
        value = request(); value['requester_label'] = 'owner-admin-approved'
        for origin in (replace(ORIGIN, repository_id=999), replace(ORIGIN, branch='outbox/fixture'), replace(ORIGIN, mailbox_epoch=str(uuid4()))):
            self.assertEqual(self.observe(value, origin=origin).code, 'POLICY_DENIED')
        self.assertEqual(self.api.calls, [])

    def test_scope_alias_and_device_must_match(self):
        self.assertEqual(self.observe(request(repo_alias='other', number=1)).code, 'PROJECT_NOT_ENROLLED')
        value = request(); value['target_device'] = 'other'
        self.assertEqual(self.observe(value).code, 'POLICY_DENIED')
        self.assertEqual(self.api.calls, [])

    def test_confirmation_binds_request_and_target(self):
        self.mailbox = enrollment(decision='confirm')
        value = validate_request(request(), now=NOW)
        decision = evaluate(value, enrollment=self.mailbox, origin=ORIGIN, target_state=self.adapter.target_state(value), now=NOW)
        approval = Approval(decision.request_sha256, 1, decision.target_state_sha256, NOW + timedelta(seconds=60))
        self.assertEqual(self.observe(value).code, 'APPROVAL_REQUIRED')
        for bad in (replace(approval, revoked=True), replace(approval, expires_at=NOW), replace(approval, target_state_sha256='0'*64)):
            self.assertEqual(self.observe(value, approval=bad).code, 'APPROVAL_REQUIRED')
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.observe(value, approval=approval).code, 'OK')

    def test_single_issue_fixed_gets_and_exact_bytes(self):
        result = self.observe()
        self.assertEqual(result.code, 'OK')
        self.assertEqual(self.api.calls, ['/repos/test/project', '/repos/test/project/issues/7', '/repos/test/project'])
        self.assertEqual(payload(result)['data']['item']['number'], 7)
        files, manifest = text_group_export(result.evidence, str(uuid4()))
        for entry in manifest['entries']:
            self.assertEqual(entry['byte_length'], len(files[entry['path']]))
            self.assertEqual(entry['sha256'], hashlib.sha256(files[entry['path']]).hexdigest())

    def test_metadata_identity_visibility_and_type_changes_fail_closed(self):
        for changed in ({**META, 'id': 124}, {**META, 'id': True}, {**META, 'full_name': 'other/repo'}, {**META, 'private': False}):
            self.api.values = [changed]
            self.assertEqual(self.observe().code, 'REMOTE_IDENTITY_CHANGED')
            self.assertEqual(len(self.api.calls), 1)
            self.api.calls.clear()
        self.api.values = [dict(META), item(), {**META, 'private': False}]
        result = self.observe()
        self.assertEqual(result.code, 'REMOTE_IDENTITY_CHANGED')
        self.assertIsNone(payload(result)['data'])

    def test_state_change_during_observation_discards_result(self):
        def change():
            self.mailbox = replace(self.mailbox, outbox_branch='outbox/new')
            return dict(META)
        self.api.values[-1] = change
        self.assertEqual(self.observe().code, 'AUTHORITY_CHANGED')

    def test_expired_input_never_uses_api(self):
        value = request(); value['expires_at'] = (NOW - timedelta(seconds=1)).isoformat(); value['created_at'] = (NOW - timedelta(seconds=2)).isoformat()
        self.assertEqual(self.observe(value).code, 'EXPIRED')
        self.assertEqual(self.api.calls, [])

    def test_expiry_between_api_reads_stops_remaining_requests(self):
        clock = [NOW]
        self.adapter.clock = lambda: clock[0]
        def advance():
            clock[0] += timedelta(minutes=6)
            return dict(META)
        self.api.values = [advance]
        self.assertEqual(self.observe().code, 'EXPIRED')
        self.assertEqual(len(self.api.calls), 1)

    def test_disable_during_read_discards_observation(self):
        def disable():
            self.adapter.enabled = False
            return dict(META)
        self.api.values = [disable]
        self.assertEqual(self.observe().code, 'EXTENSION_DISABLED')
        self.assertEqual(len(self.api.calls), 1)

    def test_cancellation_before_and_during_observation_has_no_data(self):
        self.assertEqual(self.observe(cancelled=lambda: True).code, 'CANCELLED')
        self.assertEqual(self.api.calls, [])
        result = self.observe(cancelled=lambda: len(self.api.calls) >= 1)
        self.assertEqual(result.code, 'CANCELLED')
        self.assertIsNone(payload(result)['data'])

    def test_wrong_object_number_url_and_boolean_id_fail(self):
        for wrong in ({**item(), 'number': 8}, {**item(), 'url': 'https://evil.test/issues/7'}, {**item(), 'id': True}):
            self.api.values = [dict(META), wrong]
            result = self.observe()
            self.assertNotEqual(result.code, 'OK')
            self.assertIsNone(payload(result)['data'])

    def test_issue_read_refuses_pull_request(self):
        self.api.values = [dict(META), {**item(), 'pull_request': {}}]
        self.assertEqual(self.observe().code, 'NOT_AN_ISSUE')

    def test_pr_read_never_checks_out_and_has_only_three_gets(self):
        self.api.values = [dict(META), item(pr=True), dict(META)]
        result = self.observe(request('github.pr.read', repo_alias='project', number=7))
        self.assertEqual(result.code, 'OK')
        self.assertEqual(payload(result)['data']['item']['head_sha'], SHA)
        self.assertEqual(self.api.calls[1], '/repos/test/project/pulls/7')

    def test_one_page_and_pr_exclusion_are_explicit(self):
        self.api.values = [dict(META), [item(), {**item(8), 'pull_request': {}}], dict(META)]
        result = self.observe(request('github.issues.list', repo_alias='project', per_page=2, page=4, state='closed'))
        data = payload(result)['data']
        self.assertEqual(len(data['items']), 1)
        self.assertTrue(data['possibly_more'])
        self.assertEqual(data['skipped_pull_requests'], 1)
        self.assertEqual(self.api.calls[1], '/repos/test/project/issues?state=closed&page=4&per_page=2')

    def test_oversized_page_does_not_truncate_into_valid_result(self):
        self.api.values = [dict(META), [item(), item(8)]]
        result = self.observe(request('github.issues.list', repo_alias='project', per_page=1))
        self.assertEqual(result.code, 'REMOTE_MALFORMED')
        self.assertIsNone(payload(result)['data'])

    def test_check_runs_are_not_an_aggregate_pass_or_merge_decision(self):
        check = dict(id=7, url='https://api.github.com/repos/test/project/check-runs/7', name='tests', head_sha=SHA, status='in_progress', conclusion=None)
        self.api.values = [dict(META), {'check_runs': [check]}, dict(META)]
        result = self.observe(request('github.checks.read', repo_alias='project', commit_sha=SHA))
        self.assertEqual(result.code, 'OK')
        self.assertIn('not-a-merge-decision', payload(result)['data']['qualification'])
        self.assertIsNone(payload(result)['data']['items'][0]['conclusion'])
        self.assertEqual(len(self.api.calls), 3)

    def test_wrong_check_commit_discards_result(self):
        check = dict(id=7, url='https://api.github.com/repos/test/project/check-runs/7', name='tests', head_sha='b'*40, status='completed', conclusion='success')
        self.api.values = [dict(META), {'check_runs': [check]}]
        self.assertEqual(self.observe(request('github.checks.read', repo_alias='project', commit_sha=SHA)).code, 'REMOTE_IDENTITY_CHANGED')

    def test_redaction_precedes_json_escaping_and_export(self):
        self.adapter.secrets = ('SYNTHETIC_TOKEN',)
        self.adapter.paths = (r'C:\Users\Synthetic User',)
        self.api.values = [dict(META), {**item(), 'body': 'SYNTHETIC_\x1b[31mTOKEN\x1b[0m C:\\Users\\Synthetic User\\private.txt\n\x1b]52;c;aW5lcnQ=\x07'}, dict(META)]
        result = self.observe()
        text = ''.join(c.text for c in result.evidence.chunks)
        self.assertEqual(result.code, 'OK')
        for secret in ('SYNTHETIC_TOKEN', 'Synthetic User', '\x1b', 'aW5lcnQ='):
            self.assertNotIn(secret, text)
        self.assertIn('[REDACTED]', text)

    def test_remote_raw_error_text_is_not_exported(self):
        self.api.values = [ReadError('PERMISSION_DENIED')]
        result = self.observe()
        self.assertEqual(result.code, 'PERMISSION_DENIED')
        self.assertIsNone(payload(result)['data'])

    def test_local_import_bytes_and_mapping_have_same_decision(self):
        value = request()
        one = self.observe(json.dumps(value).encode())
        self.api.values = [dict(META), item(), dict(META)]
        two = self.observe(value)
        self.assertEqual(payload(one), payload(two))

    def test_denied_fields_and_unknown_caps_have_no_io(self):
        for field in ('endpoint', 'argv', 'headers', 'environment', 'shell', 'host', 'path'):
            value = request(); value['arguments'][field] = 'inert'
            self.assertEqual(self.observe(value).code, 'INVALID_REQUEST_OR_RESPONSE')
        value = request(); value['capability'] = 'github.issue.update'
        self.assertEqual(self.observe(value).code, 'INVALID_REQUEST_OR_RESPONSE')
        self.assertEqual(self.api.calls, [])

    def test_enrollment_rejects_duplicate_aliases_unknown_permissions(self):
        for bad in (replace(PROJECT, alias='../x'), replace(PROJECT, full_name='test/repo?host=elsewhere'), replace(PROJECT, repository_id=True), replace(PROJECT, capabilities=('github.issue.update',))):
            with self.assertRaises(ReadError):
                ReadAdapters(enrollment_loader=enrollment, projects=(bad,))
        with self.assertRaises(ReadError):
            ReadAdapters(enrollment_loader=enrollment, projects=(PROJECT, PROJECT))


class ContractTests(unittest.TestCase):
    def test_all_read_envelopes_canonical_and_unknown_fields_rejected(self):
        arguments = {'github.issue.read': {'repo_alias':'project','number':1}, 'github.pr.read': {'repo_alias':'project','number':1},
                     'github.issues.list': {'repo_alias':'project'}, 'github.checks.read': {'repo_alias':'project','commit_sha':SHA},
                     'git.worktree.status': {'worktree_alias':'tree'}}
        for cap, args in arguments.items():
            with self.subTest(cap=cap):
                first = validate_request(request(cap, **args), now=NOW)
                second = validate_request(first, now=NOW)
                self.assertEqual(canonical_sha256(first), canonical_sha256(second))
                with self.assertRaises(ProtocolError):
                    read_arguments(cap, {**args, 'extra': True})

    def test_numeric_boundaries_and_argument_injections(self):
        for bad in (True, False, 0, -1, 1.2, '1', 2**31):
            with self.assertRaises(ProtocolError):
                read_arguments('github.issue.read', {'repo_alias':'project','number':bad})
        for bad in ('../project', '--help', 'a/b', 'a%2fb', 'a\n', 'x:stream', '@file', '{owner}', '\\server'):
            with self.assertRaises(ProtocolError):
                read_arguments('github.issues.list', {'repo_alias':bad})
        for bad in ('main', 'a'*39, 'a'*41, 'A'*40, SHA+'?x=1'):
            with self.assertRaises(ProtocolError):
                read_arguments('github.checks.read', {'repo_alias':'project','commit_sha':bad})
        for key, bad in (('page',1001),('per_page',101),('page',True),('per_page',0)):
            with self.assertRaises(ProtocolError):
                read_arguments('github.issues.list', {'repo_alias':'project',key:bad})

    def test_schema_contains_exact_new_variants(self):
        root = Path(__file__).resolve().parents[3]
        schema = json.loads((root / 'schemas/request-v1.schema.json').read_text())
        self.assertTrue(READ_CAPABILITIES <= set(schema['properties']['capability']['enum']))
        variants = {v['if']['properties']['capability']['const']: v['then']['properties']['arguments'] for v in schema['allOf']}
        for cap in READ_CAPABILITIES:
            self.assertFalse(variants[cap]['additionalProperties'])


class GhApiTests(unittest.TestCase):
    def response(self, status=200, body=b'{}', headers=b''):
        return ProcessResult(0 if status in (200, 304) else 1, b'HTTP/2.0 ' + str(status).encode() + b' Test\r\n' + headers + b'\r\n' + body, b'SYNTHETIC_PRIVATE_ERROR')
    def api(self, response):
        self.calls = []
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            return response
        return GhReadApi(Path(sys.executable), runner=runner)

    def test_fixed_get_host_and_broker_environment(self):
        with patch.dict('os.environ', {'GH_TOKEN':'synthetic', 'GITHUB_TOKEN':'synthetic', 'SSH_AUTH_SOCK':'synthetic', 'GH_DEBUG':'api', 'GIT_CONFIG_PARAMETERS':'evil', 'PYTHONPATH':'evil'}):
            api = self.api(self.response())
            self.assertEqual(api.get('/repos/test/project').status, 200)
        argv, kw = self.calls[0]
        self.assertEqual(argv[1:8], ['api','--hostname','github.com','--include','--method','GET','-H'])
        for forbidden in ('GH_TOKEN','GITHUB_TOKEN','SSH_AUTH_SOCK','GH_DEBUG','GIT_CONFIG_PARAMETERS','PYTHONPATH'):
            self.assertNotIn(forbidden, kw['env'])
        self.assertNotIn('--paginate', argv)
        self.assertNotIn('--input', argv)
        self.assertEqual(kw['max_stdout'], 1048576)
        self.assertFalse(kw['cwd'].exists())

    def test_arbitrary_endpoint_and_write_classes_rejected(self):
        api = self.api(self.response())
        for endpoint in ('https://evil.test/', '/graphql', '/user', '/repos/test/project/issues/7/comments', '/repos/test/project?x=@file', '/repos/{owner}/{repo}', '/repos/test/project/../secrets'):
            with self.assertRaises(ReadError):
                api.get(endpoint)
        self.assertEqual(self.calls, [])

    def test_statuses_are_typed_not_guessed_from_stderr(self):
        for status, code in ((304,'NOT_MODIFIED'),(401,'AUTH_UNAVAILABLE'),(403,'PERMISSION_DENIED'),(404,'NOT_FOUND_OR_HIDDEN'),(409,'REMOTE_UNAVAILABLE'),(429,'RATE_LIMITED'),(500,'REMOTE_UNAVAILABLE')):
            with self.subTest(status=status):
                api = self.api(self.response(status))
                with self.assertRaises(ReadError) as caught:
                    api.get('/repos/test/project')
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('PRIVATE_ERROR', str(caught.exception))

    def test_retry_after_prevents_another_call(self):
        api = self.api(self.response(429, headers=b'Retry-After: 3600\r\n'))
        for _ in range(2):
            with self.assertRaises(ReadError) as caught:
                api.get('/repos/test/project')
            self.assertEqual(caught.exception.code, 'RATE_LIMITED')
        self.assertEqual(len(self.calls), 1)

    def test_zero_remaining_stops_next_get_even_after_success(self):
        api = self.api(self.response(headers=b'X-RateLimit-Remaining: 0\r\nX-RateLimit-Reset: 9999999999\r\n'))
        api.get('/repos/test/project')
        with self.assertRaises(ReadError):
            api.get('/repos/test/project')
        self.assertEqual(len(self.calls), 1)

    def test_local_attempt_limit(self):
        import time
        api = self.api(self.response()); api._attempts = [time.monotonic()] * 120
        with self.assertRaises(ReadError) as caught:
            api.get('/repos/test/project')
        self.assertEqual(caught.exception.code, 'LOCAL_RATE_BUDGET')
        self.assertEqual(self.calls, [])

    def test_duplicate_json_deep_json_html_and_no_header_are_not_success(self):
        for value in (self.response(body=b'{"a":1,"a":2}'), self.response(body=b'['*1001+b']'*1001), self.response(body=b'<html>inert</html>'), ProcessResult(0,b'{}',b'')):
            with self.assertRaises(ReadError):
                self.api(value).get('/repos/test/project')


if __name__ == '__main__':
    unittest.main()
