from __future__ import annotations
import base64
from datetime import datetime, timedelta, timezone
import random
import subprocess
import unittest

from interloc.transport.github import ApiResponse, AttemptBudget, GhCliApi, GitHubMailbox, MailboxEnrollment, PollPlanner, TransportError

A="a"*40; B="b"*40; C="c"*40; D="d"*40; E="e"*40; F="f"*40


def enroll(): return MailboxEnrollment(42,"owner/mailbox","inbox","outbox/dev-1","dev-1")


class ScriptedApi:
    def __init__(self,steps): self.steps=list(steps); self.calls=[]
    def request(self,method,endpoint,*,body=None):
        self.calls.append((method,endpoint,body))
        if not self.steps: raise AssertionError(f"unexpected call {method} {endpoint}")
        exp_method,contains,result=self.steps.pop(0)
        assert method==exp_method,(method,exp_method); assert contains in endpoint,(endpoint,contains)
        if isinstance(result,Exception): raise result
        return result


def R(data,status=200,headers=None): return ApiResponse(status,headers or {},data)

def content(raw,sha=B): return {"sha":sha,"encoding":"base64","content":base64.b64encode(raw).decode()}


class TransportTests(unittest.TestCase):
    def test_enrollment_identity_private_and_branches(self):
        api=ScriptedApi([("GET","/repositories/42",R({"id":42,"full_name":"owner/mailbox","private":True})),("GET","git/ref/heads/inbox",R({"object":{"sha":A}})),("GET","git/ref/heads/outbox%2Fdev-1",R({"object":{"sha":B}}))])
        GitHubMailbox(api,enroll()).verify_enrollment(); self.assertFalse(api.steps)

    def test_public_or_wrong_repository_fails(self):
        for data,code in [({"id":42,"full_name":"owner/mailbox","private":False},"REPOSITORY_PUBLIC"),({"id":99,"full_name":"owner/mailbox","private":True},"REPOSITORY_MISMATCH")]:
            api=ScriptedApi([("GET","/repositories/42",R(data))])
            with self.assertRaises(TransportError) as ctx: GitHubMailbox(api,enroll()).verify_enrollment()
            self.assertEqual(ctx.exception.code,code)

    def test_same_head_does_no_extra_work(self):
        api=ScriptedApi([("GET","git/ref/heads/inbox",R({"object":{"sha":A}}))])
        head,items=GitHubMailbox(api,enroll()).discover_requests(A); self.assertEqual((head,items),(A,()))

    def test_history_gap_reconciles_full_tree_not_month_path(self):
        request_path="requests/dev-1/2026-01/req.json"
        api=ScriptedApi([("GET","git/ref/heads/inbox",R({"object":{"sha":A}})),("GET","/commits?",R([])),("GET",f"/git/commits/{A}",R({"tree":{"sha":C}})),("GET",f"/git/trees/{C}",R({"truncated":False,"tree":[{"type":"blob","path":request_path,"sha":B}]})),("GET","/contents/requests/dev-1/2026-01/req.json",R(content(b"{}",B)))])
        head,items=GitHubMailbox(api,enroll()).discover_requests(D)
        self.assertEqual(head,A); self.assertEqual(len(items),1); self.assertEqual(items[0].path,request_path); self.assertEqual(items[0].commit_sha,A)

    def test_commit_and_file_pagination_catch_up_oldest_first(self):
        p1="requests/dev-1/a.json"; p2="requests/dev-1/b.json"; commits=[{"sha":A},{"sha":B},{"sha":C}]
        api=ScriptedApi([("GET","git/ref/heads/inbox",R({"object":{"sha":A}})),("GET","/commits?",R(commits)),("GET",f"/commits/{B}",R({"files":[{"filename":p2,"status":"added"}]})),("GET","/contents/requests/dev-1/b.json",R(content(b"2",D))),("GET",f"/commits/{A}",R({"files":[{"filename":p1,"status":"added"}]})),("GET","/contents/requests/dev-1/a.json",R(content(b"1",E)))])
        head,items=GitHubMailbox(api,enroll()).discover_requests(C)
        self.assertEqual([x.commit_sha for x in items],[B,A]); self.assertEqual([x.data for x in items],[b"2",b"1"])

    def test_truncated_full_tree_fails_closed(self):
        api=ScriptedApi([("GET","git/ref/heads/inbox",R({"object":{"sha":A}})),("GET",f"/git/commits/{A}",R({"tree":{"sha":C}})),("GET",f"/git/trees/{C}",R({"truncated":True,"tree":[]}))])
        with self.assertRaises(TransportError) as ctx: GitHubMailbox(api,enroll()).discover_requests(None)
        self.assertEqual(ctx.exception.code,"TREE_TRUNCATED")

    def test_full_ref_text_read_and_size_bound(self):
        api=ScriptedApi([("GET","/contents/indexes/x.json",R(content(b"hello",B)))])
        self.assertEqual(GitHubMailbox(api,enroll()).read_text_at("indexes/x.json",A),b"hello")
        api=ScriptedApi([("GET","/contents/x",R(content(b"x"*20,B)))])
        with self.assertRaises(TransportError): GitHubMailbox(api,enroll()).read_text_at("x",A,max_bytes=10)

    def publish_success_steps(self):
        return [("GET","git/ref/heads/outbox%2Fdev-1",R({"object":{"sha":A}})),("GET","/contents/indexes/g.json",TransportError("NOT_FOUND","x",status=404)),("GET",f"/git/commits/{A}",R({"tree":{"sha":B}})),("POST","/git/blobs",R({"sha":C})),("POST","/git/blobs",R({"sha":D})),("POST","/git/trees",R({"sha":E})),("POST","/git/commits",R({"sha":F})),("PATCH","git/refs/heads/outbox%2Fdev-1",R({"object":{"sha":F}}))]

    def test_atomic_publish_uses_nonforce_ref_advance_and_manifest_last_in_tree_set(self):
        api=ScriptedApi(self.publish_success_steps()); result=GitHubMailbox(api,enroll()).publish_group(group_id="deadbeef",files={"sessions/s/chunks/a.txt":b"hi"},manifest_path="indexes/g.json",manifest_bytes=b"{}")
        self.assertEqual((result.commit_sha,result.reused),(F,False)); self.assertEqual(api.calls[-1][2],{"sha":F,"force":False}); self.assertEqual(len([c for c in api.calls if c[1].endswith("/git/trees")][0][2]["tree"]),2)

    def test_duplicate_publication_is_idempotent_without_writes(self):
        api=ScriptedApi([("GET","git/ref/heads/outbox%2Fdev-1",R({"object":{"sha":F}})),("GET","/contents/indexes/g.json",R(content(b"{}",D)))])
        result=GitHubMailbox(api,enroll()).publish_group(group_id="deadbeef",files={},manifest_path="indexes/g.json",manifest_bytes=b"{}")
        self.assertTrue(result.reused); self.assertEqual(len(api.calls),2)

    def test_ref_conflict_retries_without_force_and_orphan_is_safe(self):
        steps=self.publish_success_steps(); steps[-1]=(steps[-1][0],steps[-1][1],TransportError("REF_CONFLICT","x",status=409)); steps += self.publish_success_steps()
        api=ScriptedApi(steps); result=GitHubMailbox(api,enroll()).publish_group(group_id="deadbeef",files={"sessions/s/chunks/a.txt":b"hi"},manifest_path="indexes/g.json",manifest_bytes=b"{}",retries=2)
        self.assertEqual(result.commit_sha,F); patches=[c for c in api.calls if c[0]=="PATCH"]; self.assertEqual(len(patches),2); self.assertTrue(all(p[2]["force"] is False for p in patches))

    def test_collision_and_path_size_limits_fail_before_mutation(self):
        api=ScriptedApi([("GET","git/ref/heads/outbox%2Fdev-1",R({"object":{"sha":A}})),("GET","/contents/indexes/g.json",R(content(b"other",D)))])
        with self.assertRaises(TransportError) as ctx: GitHubMailbox(api,enroll()).publish_group(group_id="deadbeef",files={},manifest_path="indexes/g.json",manifest_bytes=b"{}")
        self.assertEqual(ctx.exception.code,"PUBLICATION_COLLISION")
        with self.assertRaises(TransportError): GitHubMailbox(ScriptedApi([]),enroll()).publish_group(group_id="deadbeef",files={"../x":b"x"},manifest_path="indexes/g.json",manifest_bytes=b"{}")

    def test_poll_backoff_retry_after_and_budget(self):
        p=PollPlanner(rng=random.Random(1)); self.assertEqual(p.delay(active=True,retry_after=77),77); self.assertGreater(p.delay(active=False,failures=2),400)
        now=datetime(2026,1,1,tzinfo=timezone.utc); budget=AttemptBudget(2,clock=lambda:now); budget.consume(); budget.consume()
        with self.assertRaises(TransportError) as ctx: budget.consume()
        self.assertEqual(ctx.exception.code,"LOCAL_RATE_BUDGET")

    def test_gh_cli_status_mapping_includes_304_401_403_404_409_422_5xx(self):
        def runner_for(status):
            def runner(argv,**kwargs):
                rc=0 if status<400 else 1
                return subprocess.CompletedProcess(argv,rc,f"HTTP/2 {status}\r\n\r\n{{}}",f"HTTP {status}" if rc else "")
            return runner
        self.assertEqual(GhCliApi(runner=runner_for(304)).request("GET","/fixture").status,304)
        expected={401:"AUTH_OR_PERMISSION",403:"AUTH_OR_PERMISSION",404:"NOT_FOUND",409:"REF_CONFLICT",422:"REF_CONFLICT",500:"REMOTE_TRANSIENT",503:"REMOTE_TRANSIENT"}
        for status,code in expected.items():
            with self.subTest(status=status):
                with self.assertRaises(TransportError) as ctx: GhCliApi(runner=runner_for(status)).request("GET","/fixture")
                self.assertEqual(ctx.exception.code,code)

    def test_gh_cli_is_fixed_host_no_token_env_and_parses_retry_after(self):
        captured={}
        def runner(argv,**kwargs):
            captured.update(argv=argv,kwargs=kwargs); return subprocess.CompletedProcess(argv,0,"HTTP/2 200\r\nretry-after: 9\r\n\r\n{\"ok\":true}","")
        response=GhCliApi(runner=runner).request("GET","/rate_limit")
        self.assertEqual(response.data,{"ok":True}); self.assertIn("github.com",captured["argv"]); self.assertNotIn("GH_TOKEN",captured["kwargs"]["env"]); self.assertFalse(captured["kwargs"]["shell"])
        def limited(argv,**kwargs): return subprocess.CompletedProcess(argv,1,"HTTP/2 429\r\nRetry-After: 12\r\n\r\n{}","HTTP 429")
        with self.assertRaises(TransportError) as ctx: GhCliApi(runner=limited).request("GET","/rate_limit")
        self.assertEqual(ctx.exception.code,"RATE_LIMITED"); self.assertEqual(ctx.exception.retry_after,12)

if __name__=="__main__": unittest.main()
