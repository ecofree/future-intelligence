"""Additional adversarial ledger tests; no live model calls."""
from concurrent.futures import ThreadPoolExecutor
import time
import unittest

import psycopg
from psycopg.types.json import Jsonb
import test_task_ledger as fixtures


class TaskEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.TaskLedgerTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.TaskLedgerTests.tearDownClass.__func__(cls)

    call=fixtures.TaskLedgerTests.call
    admin=fixtures.TaskLedgerTests.admin
    question=fixtures.TaskLedgerTests.question
    register_versions=fixtures.TaskLedgerTests.register_versions
    experiment=fixtures.TaskLedgerTests.experiment
    claim=fixtures.TaskLedgerTests.claim
    count=fixtures.TaskLedgerTests.count
    state=fixtures.TaskLedgerTests.state
    succeed=fixtures.TaskLedgerTests.succeed

    def test_budget_overrun_blocks_already_claimed_dispatch(self):
        e,*_=self.experiment(2,budget_microusd=200)
        first=self.claim(e); second=self.claim(e)
        self.succeed(first,cost=250)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.r['worker'].start_call(second)
        self.r['worker'].fail(second,'BUDGET_OVERRUN',0)
        s=self.r['operator'].summary(e)
        self.assertEqual((s['spent_microusd'],s['reserved_microusd']),(250,0))

    def test_late_old_completion_cannot_overwrite_new_attempt(self):
        e,*_=self.experiment(lease_seconds=1)
        old=self.claim(e);self.r['worker'].start_call(old);time.sleep(1.1)
        self.r['operator'].recover(e)
        self.r['operator'].reconcile(old['attempt_id'],3,'FIXTURE reconciliation',True)
        new=self.claim(e); self.succeed(new,cost=7)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.r['worker'].succeed(old,[0,1000000],'c'*64,3)
        self.assertEqual(self.admin('SELECT attempt_id::text FROM fi.task_submissions WHERE task_id=%s',(old['task_id'],)),new['attempt_id'])
        self.assertEqual(self.r['operator'].summary(e)['spent_microusd'],10)

    def test_concurrent_same_completion_is_charged_once(self):
        e,*_=self.experiment();lease=self.claim(e);self.r['worker'].start_call(lease)
        with ThreadPoolExecutor(3) as pool:
            receipts=list(pool.map(lambda _:self.r['worker'].succeed(lease,[400000,600000],'d'*64,9),range(3)))
        self.assertEqual(len(set(receipts)),1)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_submissions WHERE task_id=%s',(lease['task_id'],)),1)
        self.assertEqual(self.r['operator'].summary(e)['spent_microusd'],9)

    def test_lease_rechecked_after_budget_lock_wait(self):
        e,*_=self.experiment(lease_seconds=1);lease=self.claim(e)
        with psycopg.connect(fixtures.DSN) as blocker, ThreadPoolExecutor(1) as pool:
            blocker.execute('SELECT 1 FROM fi.budget_accounts WHERE experiment_id=%s FOR UPDATE',(e,))
            pending=pool.submit(self.r['worker'].heartbeat,lease)
            # Observe an actual database wait, not just a thread scheduling delay.
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                waiting=self.admin("SELECT count(*) FROM pg_stat_activity WHERE usename=%s AND wait_event_type='Lock'",(self.logins['worker'],))
                if waiting: break
                time.sleep(.02)
            self.assertGreater(waiting,0,'heartbeat never waited on the held budget lock')
            time.sleep(1.1);blocker.commit()
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): pending.result(timeout=5)

    def test_two_versions_of_same_question_are_not_independent_tasks(self):
        v,pid=self.question()
        old=self.author.get(v)
        later=self.author.create_version(old['external_key'],old['spec'],[str(pid)],v)
        self.reviewer.review(later,'fixture second revision');self.author.freeze(later)
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.register_versions([v,later])

    def test_worker_cannot_rewrite_submission_or_manifest(self):
        e,*_=self.experiment();lease=self.claim(e);self.succeed(lease)
        for statement,params in [
            ('DELETE FROM fi.task_submissions WHERE task_id=%s RETURNING task_id',(lease['task_id'],)),
            ('UPDATE fi.experiments SET spec=spec WHERE id=%s RETURNING id',(e,)),
            ('TRUNCATE fi.expected_tasks',())]:
            with self.subTest(statement=statement),self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.call('operator',statement,params)
