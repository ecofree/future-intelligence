"""Real lock waits, not sleeps pretending to prove concurrency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

import test_postgres as base
from future_intelligence.db.repository import QuestionRepository


class TransactionEdgeTests(unittest.TestCase):
    # Reuse fixture helpers, not inherited test methods (which would run twice).
    setUpClass = classmethod(base.PostgresTests.setUpClass.__func__)
    tearDownClass = classmethod(base.PostgresTests.tearDownClass.__func__)
    call = base.PostgresTests.call
    source = base.PostgresTests.source
    question = base.PostgresTests.question
    reviewed = base.PostgresTests.reviewed

    def wait_for_lock(self, app_name):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with psycopg.connect(base.DSN) as c:
                blocked = c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'", (app_name,)).fetchone()[0]
            if blocked:
                return
            time.sleep(0.02)
        self.fail('worker did not reach actual PostgreSQL lock wait')

    def blocked_freeze(self, vid, blocker, release_after=None):
        app = 'fi_lock_test_' + uuid4().hex
        worker = QuestionRepository(make_conninfo(self.dsns['author'], application_name=app))
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(worker.freeze, vid)
        try:
            self.wait_for_lock(app)
            if release_after:
                delay = (release_after - datetime.now(timezone.utc)).total_seconds()
                if delay > 0: time.sleep(delay + 0.05)
            blocker.commit()
            return future.result(timeout=10)
        finally:
            blocker.close()  # releases lock on assertion failure too
            pool.shutdown(wait=True)

    def test_reject_snapshot_isolation_for_permission_admission(self):
        _,vid,_ = self.reviewed()
        for level in ['REPEATABLE READ', 'SERIALIZABLE']:
            with self.subTest(level=level), self.assertRaises(psycopg.errors.InvalidTransactionState):
                with psycopg.connect(self.dsns['author']) as c:
                    c.execute('SET TRANSACTION ISOLATION LEVEL ' + level)
                    c.execute('SELECT fi.freeze_version(%s)', (vid,))

    def test_revocation_committing_during_lock_wait_is_seen(self):
        _,vid,pid = self.reviewed()
        blocker = psycopg.connect(self.dsns['source_admin'])
        blocker.execute('SELECT fi.revoke_source_permission(%s,%s)', (pid,'Concurrent test revocation'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.blocked_freeze(vid,blocker)
        self.assertEqual(self.repos['reader'].get(vid)['status'], 'REVIEWED')

    def test_all_sources_checked_after_last_lock_acquired(self):
        # Expire the first source while waiting on the last, reproducing the
        # per-source check-before-wait bug that migration 0002 closes.
        a = self.source(); b = self.source()
        first,last = sorted([a,b],key=lambda item:str(item[0]))
        now = datetime.now(timezone.utc); expiry = now + timedelta(seconds=4)
        pid = self.call('source_admin','SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',
            (first[0],base.USES,now-timedelta(seconds=1),expiry,'Short-lived fixture grant'))
        vid = self.repos['author'].create_version('lock-'+uuid4().hex,base.example_spec(),[str(pid),str(last[1])])
        self.repos['reviewer'].review(vid,'Both valid now')
        blocker = psycopg.connect(base.DSN)
        blocker.execute('SELECT id FROM fi.sources WHERE id=%s FOR UPDATE',(last[0],))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.blocked_freeze(vid,blocker,release_after=expiry)

    def test_deadline_rechecked_after_wait(self):
        _,pid = self.source()
        spec=base.example_spec(); deadline=datetime.now(timezone.utc)+timedelta(seconds=4)
        spec['forecast_deadline']=deadline.isoformat()
        _,vid,_=self.question(spec=spec,pid=pid)
        self.repos['reviewer'].review(vid,'Before cutoff')
        blocker=psycopg.connect(base.DSN)
        blocker.execute('SELECT version_id FROM fi.question_states WHERE version_id=%s FOR UPDATE',(vid,))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.blocked_freeze(vid,blocker,release_after=deadline)

    def test_transaction_rollback_does_not_leave_question(self):
        _,pid=self.source(); key='rollback-'+uuid4().hex
        with psycopg.connect(self.dsns['author']) as c:
            c.execute('SELECT fi.create_version(%s,%s,%s::uuid[],NULL)',(key,Jsonb(base.example_spec()),[str(pid)]))
            c.rollback()
        with psycopg.connect(base.DSN) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM fi.questions WHERE external_key=%s',(key,)).fetchone()[0],0)

    def test_missing_version_is_explicit(self):
        with self.assertRaises(LookupError): self.repos['reader'].get(str(uuid4()))


if __name__ == '__main__': unittest.main()
