"""Real PostgreSQL tests. Missing DSN/driver is an error, never a successful skip."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_question_spec import example_spec
from future_intelligence.db.migrate import migrate
from future_intelligence.db.repository import QuestionRepository

DSN=os.environ.get('FI_TEST_DATABASE_URL')
if not DSN:
    raise RuntimeError('FI_TEST_DATABASE_URL required for real PostgreSQL tests')

USES=['internal_research','model_processing','model_evaluation','storage']


class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DSN)
        cls.logins={}; cls.dsns={}; cls.repos={}
        suffix=uuid4().hex[:10]
        with psycopg.connect(DSN) as c:
            for role in ('author','reviewer','source_admin','reader','resolver'):
                login=f'fi_test_{role}_{suffix}'
                password=uuid4().hex
                c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT PASSWORD {}').format(
                    sql.Identifier(login),sql.Literal(password)))
                c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier('fi_'+role),sql.Identifier(login)))
                cls.logins[role]=login
                cls.dsns[role]=make_conninfo(DSN,user=login,password=password)
                cls.repos[role]=QuestionRepository(cls.dsns[role])

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN) as c:
            for login in cls.logins.values():
                c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(login)))

    def call(self, role, statement, params=()):
        with psycopg.connect(self.dsns[role]) as c:
            return c.execute(statement,params).fetchone()[0]

    def source(self, uses=None, start=None, end=None):
        now=datetime.now(timezone.utc)
        sid=self.call('source_admin','SELECT fi.register_source(%s,%s)',('Synthetic fixture','https://example.invalid/fixture'))
        pid=self.call('source_admin','SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',
            (sid,USES if uses is None else uses,start or now-timedelta(days=1),end or now+timedelta(days=10),'TEST FIXTURE license, not a real authorization'))
        return sid,pid

    def question(self, spec=None, pid=None):
        if pid is None: _,pid=self.source()
        key='test-'+uuid4().hex
        vid=self.repos['author'].create_version(key,spec or example_spec(),[str(pid)])
        return key,vid,pid

    def reviewed(self):
        key,vid,pid=self.question()
        self.repos['reviewer'].review(vid,'Fixture review')
        return key,vid,pid

    def test_migration_is_idempotent(self):
        self.assertEqual(migrate(DSN),[])

    def test_migration_checksum_guard(self):
        with psycopg.connect(DSN) as c:
            original=c.execute('SELECT sha256 FROM fi_meta.schema_migrations WHERE version=%s',('0001_question_ledger.sql',)).fetchone()[0]
            c.execute('UPDATE fi_meta.schema_migrations SET sha256=%s WHERE version=%s',('bad','0001_question_ledger.sql'))
        try:
            with self.assertRaises(RuntimeError): migrate(DSN)
        finally:
            with psycopg.connect(DSN) as c:
                c.execute('UPDATE fi_meta.schema_migrations SET sha256=%s WHERE version=%s',(original,'0001_question_ledger.sql'))

    def test_create_review_freeze_and_reconnect(self):
        key,vid,_=self.reviewed()
        digest=self.repos['author'].freeze(vid)
        # Repository reconnects for every call: nothing relies on in-memory state.
        value=QuestionRepository(self.dsns['reader']).get(vid)
        self.assertEqual(value['status'],'FROZEN')
        self.assertEqual(value['external_key'],key)
        self.assertEqual(hashlib.sha256(value['snapshot_text'].encode()).hexdigest(),digest)
        self.assertEqual(value['spec']['data_revision_policy'],'FIRST_RELEASE')
        self.assertEqual(value['original_timezone'],'Asia/Tokyo')
        self.assertEqual(value['reviewed_by'],self.logins['reviewer'])

    def test_draft_cannot_freeze(self):
        _,vid,_=self.question()
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.repos['author'].freeze(vid)

    def test_separation_of_roles(self):
        _,vid,_=self.question()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['author'].review(vid,'forbidden')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['reader'].freeze(vid)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['resolver'].freeze(vid)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('author','SELECT fi.register_source(%s,%s)',('forged','https://example.invalid'))

    def test_same_login_cannot_review_own_question_even_with_both_roles(self):
        _,vid,_=self.question()
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL('GRANT fi_reviewer TO {}').format(sql.Identifier(self.logins['author'])))
        try:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['author'].review(vid,'self review')
        finally:
            with psycopg.connect(DSN) as c:
                c.execute(sql.SQL('REVOKE fi_reviewer FROM {}').format(sql.Identifier(self.logins['author'])))

    def test_direct_table_writes_denied(self):
        _,vid,_=self.question()
        for statement in ["UPDATE fi.question_states SET status='FROZEN' WHERE version_id=%s",
                          'DELETE FROM fi.question_versions WHERE id=%s']:
            with self.subTest(statement=statement),self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.call('author',statement,(vid,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.call('reader','TRUNCATE fi.audit_events')

    def test_owner_ordinary_mutation_is_blocked_by_trigger(self):
        _,vid,_=self.reviewed(); self.repos['author'].freeze(vid)
        for statement in ['UPDATE fi.question_versions SET spec=spec WHERE id=%s',
                          'DELETE FROM fi.question_versions WHERE id=%s']:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                with psycopg.connect(DSN) as c: c.execute(statement,(vid,))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            with psycopg.connect(DSN) as c: c.execute('TRUNCATE fi.audit_events')

    def test_new_version_does_not_change_frozen_version(self):
        key,old,pid=self.reviewed(); self.repos['author'].freeze(old)
        spec=example_spec(); spec['data_revision_policy']='LATEST_AT_RESOLUTION_DEADLINE'
        new=self.repos['author'].create_version(key,spec,[str(pid)],expected_parent=old)
        self.assertEqual(self.repos['reader'].get(old)['status'],'FROZEN')
        self.assertEqual(self.repos['reader'].get(new)['status'],'DRAFT')
        self.assertEqual(self.repos['reader'].get(new)['parent_id'],old)
        self.assertNotEqual(self.repos['reader'].get(old)['snapshot_sha256'],self.repos['reader'].get(new)['snapshot_sha256'])

    def test_stale_parent_rejected(self):
        key,vid,pid=self.question()
        with self.assertRaises(psycopg.errors.SerializationFailure):
            self.repos['author'].create_version(key,example_spec(),[str(pid)])

    def test_concurrent_same_parent_only_one_winner(self):
        key,vid,pid=self.question(); spec=example_spec()
        def run():
            try: return self.repos['author'].create_version(key,spec,[str(pid)],vid)
            except psycopg.errors.SerializationFailure: return None
        with ThreadPoolExecutor(max_workers=2) as executor: results=list(executor.map(lambda _:run(),range(2)))
        self.assertEqual(sum(x is not None for x in results),1)

    def test_concurrent_freeze_idempotent_one_audit_event(self):
        _,vid,_=self.reviewed()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(lambda _:self.repos['author'].freeze(vid),range(4)))
        self.assertEqual(len(set(results)),1)
        count=self.call('reader',"SELECT count(*) FROM fi.audit_events WHERE entity_id=%s AND action='QUESTION_FROZEN'",(vid,))
        self.assertEqual(count,1)

    def test_missing_uses_denied(self):
        _,pid=self.source(uses=['internal_research'])
        _,vid,_=self.question(pid=pid)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['reviewer'].review(vid,'missing rights')

    def test_expired_and_future_permissions_denied(self):
        now=datetime.now(timezone.utc)
        for start,end in [(now-timedelta(days=2),now-timedelta(days=1)),(now+timedelta(days=1),now+timedelta(days=2))]:
            _,pid=self.source(start=start,end=end); _,vid,_=self.question(pid=pid)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['reviewer'].review(vid,'invalid interval')

    def test_revocation_between_review_and_freeze(self):
        _,vid,pid=self.reviewed()
        self.call('source_admin','SELECT fi.revoke_source_permission(%s,%s)',(pid,'Fixture revocation'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['author'].freeze(vid)
        self.assertEqual(self.repos['reader'].get(vid)['status'],'REVIEWED')

    def test_superseded_permission_denied(self):
        sid,pid=self.source(); _,vid,_=self.question(pid=pid)
        self.repos['reviewer'].review(vid,'valid before supersession')
        now=datetime.now(timezone.utc)
        self.call('source_admin','SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',(sid,[],now-timedelta(seconds=1),now+timedelta(days=1),'Restrictions changed'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.repos['author'].freeze(vid)

    def test_duplicate_or_missing_sources_denied(self):
        _,pid=self.source()
        for refs in [[],[str(pid),str(pid)],[str(uuid4())]]:
            with self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.repos['author'].create_version('bad-'+uuid4().hex,example_spec(),refs)

    def test_database_validates_bypassing_python(self):
        _,pid=self.source()
        invalids=[]
        for key,val in [('forecast_deadline','2030-01-01T00:00:00'),('domain','INVALID'),('bin_edges',None),('original_timezone','Bad/Zone')]:
            spec=example_spec(); spec[key]=val; invalids.append(spec)
        spec=example_spec(); spec['unknown']='x'; invalids.append(spec)
        spec=example_spec(); spec.update(question_type='binned_numeric',bin_edges=['2','1']); invalids.append(spec)
        for spec in invalids:
            with self.subTest(spec=spec),self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.call('author','SELECT fi.create_version(%s,%s,%s::uuid[],NULL)',('invalid-'+uuid4().hex,Jsonb(spec),[str(pid)]))

    def test_past_deadline_cannot_review(self):
        spec=example_spec(); spec['forecast_deadline']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        _,vid,_=self.question(spec=spec)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.repos['reviewer'].review(vid,'too late')

    def test_cannot_close_before_deadline_or_infer_outcome(self):
        _,vid,_=self.reviewed(); self.repos['author'].freeze(vid)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.repos['author'].close(vid)
        self.assertNotIn('outcome',self.repos['reader'].get(vid))

    def test_audit_actor_is_authenticated_login(self):
        _,vid,_=self.question()
        actor=self.call('reader',"SELECT actor FROM fi.audit_events WHERE entity_id=%s AND action='QUESTION_VERSION_CREATED'",(vid,))
        self.assertEqual(actor,self.logins['author'])

    def test_no_public_definer_or_helper_access(self):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('reader','SELECT fi.audit(%s,%s,%s)',(uuid4(),'forged',Jsonb({})))
        with psycopg.connect(DSN) as c:
            rows=c.execute("SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='fi' AND p.prosecdef AND NOT ('search_path=pg_catalog, fi, pg_temp'=ANY(p.proconfig))").fetchall()
            self.assertEqual(rows,[])


if __name__=='__main__': unittest.main()
