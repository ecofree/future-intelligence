"""Real PostgreSQL ledger tests: synthetic fixtures, no provider billing or real forecast."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_question_spec import example_spec
from test_experiment_spec import experiment_spec, entries
from future_intelligence.db.migrate import migrate
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.tasks.repository import TaskRepository
from future_intelligence.tasks.fixture_worker import run_once

DSN=os.environ.get('FI_TEST_DATABASE_URL')
if not DSN: raise RuntimeError('Real PostgreSQL required, cannot skip ledger acceptance')
USES=['internal_research','model_processing','model_evaluation','storage']


class TaskLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DSN)
        cls.logins={}; cls.dsns={}; cls.r={}; suffix=uuid4().hex[:10]
        with psycopg.connect(DSN) as c:
            for key, role in [('author','author'),('reviewer','reviewer'),('source','source_admin'),
                              ('operator','operator'),('worker','worker'),('worker2','worker'),
                              ('dispatcher','dispatcher'),('dispatcher2','dispatcher'),('resolver','resolver')]:
                login=f'fi_ledger_{key}_{suffix}'; password=uuid4().hex
                c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT PASSWORD {}').format(sql.Identifier(login),sql.Literal(password)))
                c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier('fi_'+role),sql.Identifier(login)))
                cls.logins[key]=login; cls.dsns[key]=make_conninfo(DSN,user=login,password=password)
                cls.r[key]=TaskRepository(cls.dsns[key])
        cls.author=QuestionRepository(cls.dsns['author']); cls.reviewer=QuestionRepository(cls.dsns['reviewer'])

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN) as c:
            for login in cls.logins.values(): c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(login)))

    def call(self,role,statement,params=()):
        with psycopg.connect(self.dsns[role]) as c: return c.execute(statement,params).fetchone()[0]

    def admin(self,statement,params=()):
        with psycopg.connect(DSN) as c: return c.execute(statement,params).fetchone()[0]

    def question(self, reviewed=True, numeric=False):
        now=datetime.now(timezone.utc)
        sid=self.call('source','SELECT fi.register_source(%s,%s)',('Ledger fixture','https://example.invalid/ledger'))
        pid=self.call('source','SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',
            (sid,USES,now-timedelta(days=1),now+timedelta(days=3),'Synthetic TEST permission only'))
        spec=example_spec()
        if numeric: spec.update(question_type='binned_numeric',bin_edges=['0','10'],unit='fixture')
        vid=self.author.create_version('ledger-'+uuid4().hex,spec,[str(pid)])
        if reviewed:
            self.reviewer.review(vid,'Synthetic review'); self.author.freeze(vid)
        return vid,pid

    def experiment(self,n=1,models=1,**changes):
        versions=[self.question()[0] for _ in range(n)]
        return self.register_versions(versions,models,**changes)

    def register_versions(self,versions,models=1,**changes):
        key='exp-'+uuid4().hex; spec=experiment_spec(**changes); model_entries=entries(models)
        exp=self.r['operator'].register(key,versions,model_entries,spec)
        self.r['operator'].bind_worker(exp,'model-0',self.logins['worker'])
        self.r['operator'].bind_worker(exp,'model-0' if models==1 else 'model-1',self.logins['worker2'])
        return exp,key,versions,model_entries,spec

    def claim(self,e,worker='worker'): return self.r[worker].claim(e,'FIXTURE')
    def count(self,table): return self.admin(sql.SQL('SELECT count(*) FROM fi.{}').format(sql.Identifier(table)))
    def state(self,t): return self.admin('SELECT status FROM fi.task_states WHERE task_id=%s',(t,))
    def succeed(self,lease,worker='worker',cost=10,probs=None):
        self.r[worker].start_call(lease)
        return self.r[worker].succeed(lease,probs or [300000,700000],'b'*64,cost)

    def test_atomic_registration_manifest_and_idempotence(self):
        e,key,versions,models,spec=self.experiment(2,2)
        count=self.count('outbox_events')
        same=self.r['operator'].register(key,versions[::-1],models[::-1],spec)
        self.assertEqual(e,same); self.assertEqual(self.count('outbox_events'),count)
        s=self.r['operator'].summary(e); self.assertEqual(s['total_tasks'],4)
        text=self.call('operator','SELECT manifest_text FROM fi.experiments WHERE id=%s',(e,))
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),s['manifest_sha256'])
        spec['budget_microusd']+=1
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.r['operator'].register(key,versions,models,spec)

    def test_concurrent_registration_one_manifest(self):
        v,_=self.question(); spec=experiment_spec(); key='parallel-'+uuid4().hex
        def register(_): return self.r['operator'].register(key,[v],entries(2),spec)
        with ThreadPoolExecutor(4) as pool: values=list(pool.map(register,range(4)))
        self.assertEqual(len(set(values)),1)
        self.assertEqual(self.r['operator'].summary(values[0])['total_tasks'],2)

    def test_rollback_no_orphan_tasks_or_outbox(self):
        v,_=self.question(); counts={t:self.count(t) for t in ['experiments','expected_tasks','outbox_events','budget_accounts']}
        with psycopg.connect(self.dsns['operator']) as c:
            c.execute('SELECT fi.register_experiment(%s,%s::uuid[],%s,%s)',('rollback-'+uuid4().hex,[v],Jsonb(entries()),Jsonb(experiment_spec())))
            c.rollback()
        self.assertEqual(counts,{t:self.count(t) for t in counts})

    def test_unfrozen_or_two_versions_rejected(self):
        v,_=self.question(reviewed=False)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.register_versions([v])
        with self.assertRaises(psycopg.Error):
            self.call('operator','SELECT fi.register_experiment(%s,%s::uuid[],%s,%s)',
                ('bad-'+uuid4().hex,[v,v],Jsonb(entries()),Jsonb(experiment_spec())))

    def test_database_revalidates_bypassing_python(self):
        v,_=self.question()
        for changes in [{'max_attempts':True},{'lease_seconds':0},{'run_kind':'UNMARKED'},
                        {'forecast_deadline':'2020-01-01T00:00:00Z'},{'forecast_deadline':'2035-01-01T00:00:00Z'}]:
            with self.subTest(changes=changes),self.assertRaises(psycopg.Error):
                self.call('operator','SELECT fi.register_experiment(%s,%s::uuid[],%s,%s)',
                    ('bad-'+uuid4().hex,[v],Jsonb(entries()),Jsonb(experiment_spec(**changes))))

    def test_concurrent_claim_is_one_attempt(self):
        e,*_=self.experiment()
        with ThreadPoolExecutor(4) as pool: values=list(pool.map(lambda _:self.claim(e),range(4)))
        self.assertEqual(sum(x is not None for x in values),1)
        s=self.r['operator'].summary(e); self.assertEqual(s['reserved_microusd'],100)

    def test_budget_reservation_is_atomic(self):
        e,*_=self.experiment(2,budget_microusd=100)
        with ThreadPoolExecutor(2) as pool: values=list(pool.map(lambda w:self.claim(e,w),['worker','worker2']))
        self.assertEqual(sum(x is not None for x in values),1)
        self.assertEqual(self.r['operator'].summary(e)['available_microusd'],0)

    def test_success_unique_receipt_and_replay(self):
        e,*_=self.experiment(); lease=self.claim(e)
        receipt=self.succeed(lease); count=self.count('outbox_events')
        same=self.r['worker'].succeed(lease,[300000,700000],'b'*64,10)
        self.assertEqual(receipt,same); self.assertEqual(self.count('outbox_events'),count)
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.r['worker'].succeed(lease,[200000,800000],'b'*64,10)
        self.assertIsNone(self.claim(e)); self.assertEqual(self.state(lease['task_id']),'SUCCEEDED')
        s=self.r['operator'].summary(e); self.assertEqual(s['spent_microusd'],10);self.assertEqual(s['reserved_microusd'],0)

    def test_call_start_marker_not_permission_to_resend(self):
        e,*_=self.experiment(); lease=self.claim(e)
        self.assertTrue(self.r['worker'].start_call(lease)); self.assertFalse(self.r['worker'].start_call(lease))

    def test_failure_does_not_hide_or_stop_other_task(self):
        e,*_=self.experiment(2); first=self.claim(e)
        self.r['worker'].fail(first,'FIXTURE_FAILURE',0)
        second=self.claim(e); self.assertIsNotNone(second); self.succeed(second)
        s=self.r['operator'].summary(e)
        self.assertEqual((s['total_tasks'],s['failed'],s['succeeded']),(2,1,1))

    def test_recover_before_call_releases_and_fences(self):
        e,*_=self.experiment(lease_seconds=1); old=self.claim(e); time.sleep(1.1)
        self.assertEqual(self.r['operator'].recover(e),1)
        self.assertEqual(self.r['operator'].summary(e)['reserved_microusd'],0)
        new=self.claim(e); self.assertGreater(new['fence'],old['fence'])
        for op in (self.r['worker'].start_call,self.r['worker'].heartbeat):
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): op(old)
        self.succeed(new)

    def test_dispatched_timeout_is_uncertain_not_auto_retry(self):
        e,*_=self.experiment(lease_seconds=1); lease=self.claim(e);self.r['worker'].start_call(lease);time.sleep(1.1)
        self.r['operator'].recover(e)
        self.assertEqual(self.state(lease['task_id']),'UNCERTAIN');self.assertIsNone(self.claim(e))
        s=self.r['operator'].summary(e);self.assertEqual(s['reserved_microusd'],100);self.assertEqual(s['uncertain'],1)
        self.r['operator'].reconcile(lease['attempt_id'],12,'fixture invoice, no usable response',True)
        new=self.claim(e);self.assertGreater(new['fence'],lease['fence']);self.succeed(new)
        self.assertEqual(self.r['operator'].summary(e)['spent_microusd'],22)

    def test_unknown_success_cost_is_held_not_zero(self):
        e,*_=self.experiment(2,budget_microusd=100);lease=self.claim(e);self.succeed(lease,cost=None)
        self.assertEqual(self.r['operator'].summary(e)['reserved_microusd'],100);self.assertIsNone(self.claim(e))
        self.r['operator'].reconcile(lease['attempt_id'],0,'fixture explicit no-charge evidence')
        self.assertIsNotNone(self.claim(e))

    def test_unknown_failure_blocks_until_explicit_reconciliation(self):
        e,*_=self.experiment();lease=self.claim(e);self.r['worker'].start_call(lease)
        self.r['worker'].fail(lease,'READ_TIMEOUT',None,True)
        self.assertEqual(self.state(lease['task_id']),'UNCERTAIN');self.assertIsNone(self.claim(e))
        self.r['operator'].reconcile(lease['attempt_id'],25,'fixture paid failed request',False)
        self.assertEqual(self.state(lease['task_id']),'FAILED')
        with self.assertRaises(psycopg.Error): self.r['operator'].reconcile(lease['attempt_id'],0,'different')

    def test_bounded_known_failure_retry(self):
        e,*_=self.experiment(max_attempts=2);l1=self.claim(e)
        self.r['worker'].fail(l1,'CONNECT_FAILED',0,True)
        l2=self.claim(e);self.r['worker'].fail(l2,'CONNECT_FAILED',0,True)
        self.assertEqual(self.state(l1['task_id']),'FAILED');self.assertIsNone(self.claim(e))

    def test_overrun_recorded_and_new_work_blocked(self):
        e,*_=self.experiment(2,budget_microusd=100);lease=self.claim(e);self.succeed(lease,cost=150)
        s=self.r['operator'].summary(e);self.assertEqual(s['spent_microusd'],150)
        self.assertEqual(s['available_microusd'],-50);self.assertIsNone(self.claim(e))

    def test_cancel_running_keeps_uncertain_charge(self):
        e,*_=self.experiment();lease=self.claim(e);self.r['worker'].start_call(lease)
        self.r['operator'].cancel(lease['task_id'],'fixture operator cancellation')
        self.assertEqual(self.state(lease['task_id']),'CANCELLED')
        self.assertEqual(self.r['operator'].summary(e)['reserved_microusd'],100)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.succeed(lease)

    def test_cancel_undispatched_releases_cost(self):
        e,*_=self.experiment();lease=self.claim(e);self.r['operator'].cancel(lease['task_id'],'fixture stop')
        self.assertEqual(self.r['operator'].summary(e)['reserved_microusd'],0)

    def test_completion_requires_started_call(self):
        e,*_=self.experiment();lease=self.claim(e)
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.r['worker'].succeed(lease,[1,999999],'b'*64,1)

    def test_invalid_probability_rolls_back(self):
        e,*_=self.experiment();lease=self.claim(e);self.r['worker'].start_call(lease)
        for probs in ([1,2],[True,999999],[-1,1000001],[0.5,999999.5],[1000000]):
            with self.subTest(probs=probs),self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.r['worker'].succeed(lease,probs,'b'*64,10)
        self.assertEqual(self.state(lease['task_id']),'RUNNING')
        self.assertEqual(self.r['operator'].summary(e)['reserved_microusd'],100)

    def test_numeric_distribution_length(self):
        v,_=self.question(numeric=True);e,*_=self.register_versions([v]);lease=self.claim(e)
        self.succeed(lease,probs=[100000,200000,700000])
        self.assertEqual(self.state(lease['task_id']),'SUCCEEDED')

    def test_worker_bound_to_one_entry_and_cannot_see_peer(self):
        e,*_=self.experiment(models=2);l1=self.claim(e);l2=self.claim(e,'worker2')
        self.assertEqual(l1['entry']['entry_key'],'model-0');self.assertEqual(l2['entry']['entry_key'],'model-1')
        with self.assertRaises(psycopg.Error): self.r['worker2'].start_call(l1)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.r['operator'].bind_worker(e,'model-1',self.logins['worker'])
        for role in ('worker','resolver'):
            for table in ('task_submissions','task_attempts','experiments','task_catalog'):
                with self.subTest(role=role,table=table),self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self.call(role,sql.SQL('SELECT count(*) FROM fi.{}').format(sql.Identifier(table)))

    def test_direct_writes_and_internal_helpers_denied(self):
        e,*_=self.experiment();lease=self.claim(e)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('operator',"UPDATE fi.task_states SET status='SUCCEEDED' WHERE task_id=%s RETURNING task_id",(lease['task_id'],))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.call('worker','SELECT fi.ledger_lock(%s)',(lease['task_id'],))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.r['resolver'].recover(e)

    def test_no_public_function_access(self):
        self.assertFalse(self.admin("SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace, LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='fi' AND a.grantee=0 AND a.privilege_type='EXECUTE')"))

    def test_fixture_runner_and_live_guard(self):
        e,*_=self.experiment();result=run_once(self.r['worker'],e)
        self.assertEqual(result['run_kind'],'FIXTURE')
        self.assertEqual(self.r['operator'].summary(e)['succeeded'],1)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.r['worker'].claim(e,'LIVE')

    def test_permission_revocation_at_call_gate(self):
        v,pid=self.question();e,*_=self.register_versions([v]);lease=self.claim(e)
        self.call('source','SELECT fi.revoke_source_permission(%s,%s)',(pid,'fixture withdrawal'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.r['worker'].start_call(lease)
        self.r['worker'].fail(lease,'SOURCE_PERMISSION',0)

    def test_revoked_question_does_not_block_healthy_question(self):
        v,pid=self.question();w,_=self.question();e,*_=self.register_versions([v,w])
        self.call('source','SELECT fi.revoke_source_permission(%s,%s)',(pid,'fixture withdrawal'))
        lease=self.claim(e);self.assertIsNotNone(lease);self.assertEqual(lease['question_version_id'],w)
        self.succeed(lease);self.claim(e)
        s=self.r['operator'].summary(e);self.assertEqual((s['total_tasks'],s['failed'],s['succeeded']),(2,1,1))

    def test_deadline_expires_queued_tasks_not_false_outcomes(self):
        deadline=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()
        e,*_=self.experiment(2,forecast_deadline=deadline)
        time.sleep(2.1);self.assertIsNone(self.claim(e));self.assertEqual(self.r['operator'].recover(e),2)
        s=self.r['operator'].summary(e);self.assertEqual((s['total_tasks'],s['failed']),(2,2))

    def test_active_completion_past_lease_rejected(self):
        e,*_=self.experiment(lease_seconds=1);lease=self.claim(e);self.r['worker'].start_call(lease);time.sleep(1.1)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.r['worker'].succeed(lease,[500000,500000],'b'*64,10)

    def test_snapshot_isolation_rejected(self):
        e,*_=self.experiment()
        with psycopg.connect(self.dsns['worker']) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            with self.assertRaises(psycopg.errors.InvalidTransactionState): c.execute('SELECT fi.claim_task(%s,%s)',(e,'FIXTURE'))

    def test_outbox_redelivery_and_fence(self):
        self.experiment()
        # Isolate the event selected, without acknowledging unrelated prior rows.
        event=self.r['dispatcher'].claim_event(1);self.assertIsNotNone(event);time.sleep(1.1)
        again=self.r['dispatcher2'].claim_event(30)
        self.assertEqual(again['event_id'],event['event_id']);self.assertGreater(again['fence'],event['fence'])
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.r['dispatcher'].ack_event(event)
        self.r['dispatcher2'].ack_event(again);self.r['dispatcher2'].ack_event(again)

    def test_all_registered_task_states_remain_in_denominator(self):
        e,*_=self.experiment(3);lease=self.claim(e);self.r['worker'].fail(lease,'UNAVAILABLE',0)
        self.r['operator'].cancel(self.admin('SELECT id FROM fi.expected_tasks WHERE experiment_id=%s AND id<>%s ORDER BY id LIMIT 1',(e,lease['task_id'])),'fixture')
        self.assertEqual(self.r['operator'].summary(e)['total_tasks'],3)
