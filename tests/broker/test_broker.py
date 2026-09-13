from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from interloc.broker import BrokerError, BrokerLock, CapabilityRegistry, CapabilitySpec, Dispatcher, Journal
from interloc.policy import Decision
from interloc.protocol import canonical_sha256, validate_request

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
EPOCH = str(uuid4())


def request(capability="system.ping", request_id=None, ttl=300):
    return {"schema_version":1,"request_id":request_id or str(uuid4()),"mailbox_epoch":EPOCH,"target_device":"dev-box-1","requester_label":"x","created_at":NOW.isoformat().replace("+00:00","Z"),"expires_at":(NOW+timedelta(seconds=ttl)).isoformat().replace("+00:00","Z"),"capability":capability,"arguments":{"nonce":"n"}}


def decision(req, action="allow", revision=1):
    normalized=validate_request(req,now=NOW)
    return Decision(action,"TEST","fixture",canonical_sha256(normalized),revision,"f"*64)


class BrokerTests(unittest.TestCase):
    def journal(self, root, limit=100): return Journal(Path(root)/"state.sqlite3",queue_limit=limit)

    def test_duplicate_same_digest_reuses_state_and_different_digest_quarantines(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); rid=str(uuid4()); r=request(request_id=rid)
            key,state=j.receive(42,r,decision(r),now=NOW); self.assertEqual(state,"ready")
            key2,state2=j.receive(42,r,decision(r),now=NOW); self.assertEqual((key2,state2),(key,"ready"))
            changed=dict(r); changed["arguments"]={"nonce":"different"}
            with self.assertRaises(BrokerError) as ctx: j.receive(42,changed,decision(changed),now=NOW)
            self.assertEqual(ctx.exception.code,"REQUEST_ID_COLLISION"); self.assertEqual(j.get(key)["state"],"quarantined"); j.close()

    def test_decision_digest_mismatch_is_rejected_before_insert(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); a=request(); b=request()
            with self.assertRaises(BrokerError) as ctx: j.receive(42,a,decision(b),now=NOW)
            self.assertEqual(ctx.exception.code,"DECISION_DIGEST_MISMATCH"); j.close()

    def test_queue_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp,1); r1=request(); j.receive(42,r1,decision(r1),now=NOW)
            r2=request()
            with self.assertRaises(BrokerError) as ctx: j.receive(42,r2,decision(r2),now=NOW)
            self.assertEqual(ctx.exception.code,"QUEUE_FULL"); j.close()

    def test_deny_and_confirm_states_and_approval_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp)
            r1=request(); k1,s1=j.receive(42,r1,decision(r1,"deny"),now=NOW); self.assertEqual(s1,"rejected")
            r2=request(); d2=decision(r2,"confirm",revision=7); k2,s2=j.receive(42,r2,d2,now=NOW); self.assertEqual(s2,"awaiting_approval")
            with self.assertRaises(BrokerError): j.approve(k2,decision(r2,"allow",revision=8),now=NOW)
            j.approve(k2,decision(r2,"allow",revision=7),now=NOW); self.assertEqual(j.get(k2)["state"],"ready"); j.close()

    def test_expiry_and_cancel_before_run_are_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); r=request(ttl=1); k,_=j.receive(42,r,decision(r),now=NOW)
            self.assertEqual(j.expire_due(now=NOW+timedelta(seconds=2)),1); self.assertEqual(j.get(k)["state"],"expired")
            r2=request(); k2,_=j.receive(42,r2,decision(r2),now=NOW); self.assertEqual(j.cancel(k2,now=NOW),"cancelled"); self.assertEqual(j.get(k2)["state"],"cancelled"); j.close()

    def test_dispatch_success_publication_retry_and_ack_do_not_rerun_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); calls=[]; reg=CapabilityRegistry(); reg.register(CapabilitySpec("system.ping",lambda req,ctx:(calls.append(ctx.attempt_id) or {"ok":True,"artifact_ids":["a"]})))
            r=request(); k,_=j.receive(42,r,decision(r),now=NOW); self.assertEqual(Dispatcher(j,reg).execute(k,now=NOW),"result_saved"); self.assertEqual(len(calls),1)
            j.mark_published(k,"commit:path",now=NOW); j.mark_published(k,"commit:path",now=NOW); j.mark_notification_pending(k,"notice-1",now=NOW); j.acknowledge(k,now=NOW)
            self.assertEqual(j.get(k)["state"],"acknowledged"); self.assertEqual(len(calls),1); j.close()

    def test_handler_failure_is_durable_failed_not_crash_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); reg=CapabilityRegistry()
            def boom(req,ctx): raise ValueError("synthetic")
            reg.register(CapabilitySpec("system.ping",boom))
            r=request(); k,_=j.receive(42,r,decision(r),now=NOW); self.assertEqual(Dispatcher(j,reg).execute(k,now=NOW),"failed"); self.assertEqual(j.get(k)["state"],"failed"); j.close()

    def test_crash_recovery_readonly_opt_in_vs_mutating_indeterminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); reg=CapabilityRegistry(); reg.register(CapabilitySpec("system.ping",lambda r,c:{},retry_after_crash=True))
            r=request(); k,_=j.receive(42,r,decision(r),now=NOW); j.start(k,possible_effect=False,now=NOW)
            self.assertEqual(j.recover_running(reg,now=NOW)["resumed"],1); self.assertEqual(j.get(k)["state"],"ready")
            j._db.execute("UPDATE requests SET state='running',capability='test.mutate',possible_effect=1 WHERE repository_id=? AND mailbox_epoch=? AND request_id=?",(k.repository_id,k.mailbox_epoch,k.request_id))
            reg.register(CapabilitySpec("test.mutate",lambda r,c:{},mutating=True))
            recovered=j.recover_running(reg,now=NOW); self.assertEqual(recovered["indeterminate"],1); self.assertEqual(j.get(k)["state"],"indeterminate"); j.close()

    def test_running_cancel_is_a_signal_then_handler_persists_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=self.journal(tmp); reg=CapabilityRegistry()
            def handler(req,ctx):
                j.cancel(ctx.key,now=NOW)
                self.assertTrue(ctx.cancellation_requested())
                return {"artifact_ids":[]}
            reg.register(CapabilitySpec("system.ping",handler))
            r=request(); k,_=j.receive(42,r,decision(r),now=NOW); self.assertEqual(Dispatcher(j,reg).execute(k,now=NOW),"cancelled"); self.assertEqual(j.get(k)["state"],"cancelled"); j.close()

    def test_registry_rejects_duplicates_and_mutating_auto_retry(self):
        reg=CapabilityRegistry(); reg.register(CapabilitySpec("test.read",lambda r,c:{}))
        with self.assertRaises(BrokerError): reg.register(CapabilitySpec("test.read",lambda r,c:{}))
        with self.assertRaises(BrokerError): CapabilitySpec("test.write",lambda r,c:{},mutating=True,retry_after_crash=True).validate()

    def test_database_reopen_preserves_state_and_unknown_newer_schema_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"state.sqlite3"; j=Journal(path); r=request(); k,_=j.receive(42,r,decision(r),now=NOW); j.close()
            j2=Journal(path); self.assertEqual(j2.get(k)["state"],"ready"); j2._db.execute("PRAGMA user_version=999"); j2.close()
            with self.assertRaises(BrokerError) as ctx: Journal(path)
            self.assertEqual(ctx.exception.code,"STATE_NEWER_VERSION")

    def test_single_owner_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); first=BrokerLock(root); second=BrokerLock(root); first.acquire()
            try:
                with self.assertRaises(BrokerError) as ctx: second.acquire()
                self.assertEqual(ctx.exception.code,"BROKER_ALREADY_RUNNING")
            finally: first.release(); second.release()

if __name__=="__main__": unittest.main()
