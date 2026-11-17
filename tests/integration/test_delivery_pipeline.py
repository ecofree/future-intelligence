"""Real PostgreSQL + real local crypto, synthetic events only; never live acceptance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tsa_fixture import fixture_tsa,timestamp
from future_intelligence.db.migrate import migrate
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.tasks.repository import TaskRepository
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.delivery.timestamps import TrustProfile
from future_intelligence.delivery.batch import verify_pair,seal
from future_intelligence.delivery.exports import verify_export
from future_intelligence.delivery.evaluation import score

DSN=os.environ.get('FI_TEST_DATABASE_URL')
if not DSN:raise RuntimeError('real PostgreSQL required; no synthetic database skip')
USES=['internal_research','model_processing','model_evaluation','storage']
PLAN=dict(protocol='fi.analysis.v1',seed=73,resamples=200,alpha_ppm=50000,min_clusters=20,comparisons=[['model-0','model-1']],stopping_rule='FIXED_REGISTERED_BATCH')


class DeliveryPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DSN);cls.dsns={};cls.logins={};cls.d={};cls.t={}
        roles={'author':'author','reviewer':'reviewer','source':'source_admin','operator':'operator','worker':'worker',
               'worker2':'worker','resolver':'resolver','review':'resolution_reviewer','verifier':'verifier','scorer':'scorekeeper','dashboard':'dashboard'}
        with psycopg.connect(DSN) as c:
            for label,role in roles.items():
                login='fi_delivery_'+label+'_'+uuid4().hex[:8];pw=uuid4().hex
                c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT PASSWORD {}').format(sql.Identifier(login),sql.Literal(pw)))
                c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier('fi_'+role),sql.Identifier(login)))
                cls.logins[label]=login;cls.dsns[label]=make_conninfo(DSN,user=login,password=pw)
                cls.d[label]=DeliveryRepository(cls.dsns[label]);cls.t[label]=TaskRepository(cls.dsns[label])
        cls.tmp=tempfile.TemporaryDirectory();cls.ca,cls.tsa=fixture_tsa(Path(cls.tmp.name))
        cls.profile=TrustProfile(hashlib.sha256(cls.ca).hexdigest(),('1.3.6.1.4.1.55555.1',),fixture=True)
        cls.profile_json=json.loads(json.dumps(asdict(cls.profile)))
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        with psycopg.connect(DSN) as c:
            for login in cls.logins.values():c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(login)))
    def admin(self,statement,params=()):
        with psycopg.connect(DSN) as c:return c.execute(statement,params).fetchone()[0]
    def setup_experiment(self,quick=False,numeric=False,policy='FIRST_RELEASE',models=2):
        now=datetime.now(timezone.utc);offset=6 if quick else 86400
        source=self.d['source'].one('SELECT fi.register_source(%s,%s)',('TEST synthetic release','https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0'))
        permission=self.d['source'].one('SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',
            (source,USES,now-timedelta(days=1),now+timedelta(days=10),'TEST fixture permission; no actual license claim'))
        spec=dict(title='SYNTHETIC future release',event_definition='TEST ONLY - not real-world performance',domain='MACRO',track='FAST',
                  question_type='binned_numeric' if numeric else 'binary',event_cluster='fixture-'+uuid4().hex,
                  original_timezone='Asia/Tokyo',forecast_deadline=(now+timedelta(seconds=offset)).isoformat(),
                  outcome_not_before=(now+timedelta(seconds=offset+.3)).isoformat(),
                  resolution_deadline=(now+timedelta(seconds=offset+1.5)).isoformat(),
                  resolution_rule='TEST designated release only, missing is not false',data_revision_policy=policy,
                  unit='fixture' if numeric else 'boolean',bin_edges=['0','10'] if numeric else [])
        author=QuestionRepository(self.dsns['author']);review=QuestionRepository(self.dsns['reviewer'])
        version=author.create_version('delivery-'+uuid4().hex,spec,[str(permission)])
        review.review(version,'Independent synthetic fixture review');author.freeze(version)
        entries=[dict(entry_key=f'model-{i}',provider='fixture',model_id=f'fixture-{i}',prompt_sha256='a'*64,parameters={}) for i in range(models)]
        exp_spec=dict(protocol='fi.experiment.v1',suite='A',run_kind='FIXTURE',forecast_deadline=spec['forecast_deadline'],
                      budget_microusd=10000,attempt_cap_microusd=100,max_attempts=2,lease_seconds=3600,
                      failure_policy='REPORT_MISSING',baseline_policy='UNIFORM_CATEGORIES_V1',disclosure_policy='COMPLETE_BATCH_AFTER_RESOLUTION')
        exp=self.t['operator'].register('delivery-exp-'+uuid4().hex,[version],entries,exp_spec)
        self.t['operator'].bind_worker(exp,'model-0',self.logins['worker'])
        if models>1:self.t['operator'].bind_worker(exp,'model-1',self.logins['worker2'])
        return dict(experiment=exp,version=version,permission=str(permission),source=str(source),spec=spec,models=models)
    def registration(self,x):
        plan=PLAN if x['models']>1 else {**PLAN,'comparisons':[]}
        bid=self.d['operator'].capture(x['experiment'],'REGISTRATION',plan,self.profile_json)
        return self.d['operator'].seal(bid)
    def anchor(self,batch):
        rid,q=self.d['operator'].new_anchor(batch['batch_id'])
        self.d['operator'].receive(rid,timestamp(self.tsa,q))
        report=self.d['verifier'].verify_anchor(rid,self.ca,self.profile)
        return rid,report
    def complete(self,x,worker='worker',probs=None):
        r=self.t[worker];lease=r.claim(x['experiment'],'FIXTURE');r.start_call(lease)
        r.succeed(lease,probs or ([200000,300000,500000] if x['spec']['question_type']=='binned_numeric' else [300000,700000]),'b'*64,10)
        return lease
    def final(self,x):
        bid=self.d['operator'].capture(x['experiment'],'FORECAST');return self.d['operator'].seal(bid)
    def wait_result(self,x,latest=False):
        target=datetime.fromisoformat(x['spec']['resolution_deadline' if latest else 'outcome_not_before'])
        time.sleep(max(0,(target-datetime.now(timezone.utc)).total_seconds())+.02)
    def evidence(self,x,value='1',revision=0,published=None):
        return self.d['resolver'].add_evidence(x['version'],x['permission'],'release-'+str(revision),
            published or x['spec']['outcome_not_before'],revision,value,hashlib.sha256(('fixture-'+value).encode()).hexdigest(),'TEST official release document reference')
    def resolve(self,x,evidence,kind='RESOLVED',parent=None):
        p=self.d['resolver'].propose(x['version'],kind,evidence,'Checked original publication, target period, revision and completeness; TEST fixture.',parent)
        self.d['review'].confirm(p,'Independently reviewed TEST source and frozen rule');return p

    def test_atomic_capture_and_seal(self):
        x=self.setup_experiment();r=self.registration(x)
        self.assertEqual(r,self.d['operator'].seal(r['batch_id']))
        self.assertEqual(len(r['records']),3)
        self.assertEqual(self.d['operator'].capture(x['experiment'],'REGISTRATION',PLAN,self.profile_json),r['batch_id'])
        with self.assertRaises(psycopg.errors.InvalidParameterValue):self.d['operator'].capture(x['experiment'],'REGISTRATION',{**PLAN,'seed':74},self.profile_json)
    def test_sql_and_python_merkle_agree(self):
        x=self.setup_experiment();r=self.registration(x);self.complete(x);f=self.final(x)
        self.assertEqual(len(verify_pair(r,f)[1]),3)
        self.assertEqual(sum(json.loads(t)['submission'] is None for t in f['records'][1:]),1)
    def test_registration_after_attempt_rejected(self):
        x=self.setup_experiment();self.complete(x)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.registration(x)
    def test_concurrent_capture_single_batch(self):
        x=self.setup_experiment()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(lambda _:self.d['operator'].capture(x['experiment'],'REGISTRATION',PLAN,self.profile_json),range(2)))
        self.assertEqual(ids[0],ids[1])
    def test_wrong_root_rejected(self):
        x=self.setup_experiment();bid=self.d['operator'].capture(x['experiment'],'REGISTRATION',PLAN,self.profile_json)
        rows=self.d['operator'].one('SELECT jsonb_agg(record_text ORDER BY ordinal) FROM fi.batch_records WHERE batch_id=%s',(bid,))
        r=seal(bid,'REGISTRATION',rows)
        with self.assertRaises(psycopg.errors.InvalidParameterValue):self.d['operator'].one('SELECT fi.seal_batch(%s,%s,%s,%s)',(bid,r['nonces'],'f'*64,r['subject_sha256']))
    def test_close_prevents_new_or_late_predictions(self):
        x=self.setup_experiment();self.registration(x);lease=self.t['worker'].claim(x['experiment'],'FIXTURE');self.t['worker'].start_call(lease);self.final(x)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.t['worker'].succeed(lease,[500000,500000],'b'*64,10)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.t['worker2'].claim(x['experiment'],'FIXTURE')
    def test_crypto_and_export_reverify(self):
        x=self.setup_experiment();r=self.registration(x);self.anchor(r);self.complete(x);f=self.final(x);self.anchor(f)
        bundle=self.d['operator'].export(x['experiment']);result=verify_export(bundle,self.ca,self.profile)
        self.assertTrue(result['precise_time_verified']);self.assertFalse(result['time_eligible_live']);self.assertTrue(result['fixture'])
    def test_network_failure_never_verified(self):
        x=self.setup_experiment();r=self.registration(x);rid,q=self.d['operator'].new_anchor(r['batch_id'])
        self.d['operator'].receive(rid,None,'TSA_UNAVAILABLE')
        self.assertIsNone(self.d['operator'].one('SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s',(r['batch_id'],)))
    def test_operator_cannot_self_verify(self):
        x=self.setup_experiment();r=self.registration(x);rid,q=self.d['operator'].new_anchor(r['batch_id'])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.d['operator'].one('SELECT fi.attest_anchor(%s,%s)',(rid,Jsonb({})))
    def test_resolver_cannot_see_probabilities_or_time_records(self):
        for table in ('batch_records','task_submissions','score_rows','anchor_requests'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.d['resolver'].one('SELECT count(*) FROM fi.'+table)
    def test_ots_is_independent_pending_state(self):
        x=self.setup_experiment();r=self.registration(x);rid,_=self.d['operator'].new_anchor(r['batch_id'],'OTS',b'test request')
        self.d['operator'].receive(rid,b'test pending proof')
        self.assertEqual(self.d['operator'].one('SELECT status FROM fi.anchor_replies WHERE request_id=%s',(rid,)),'PENDING_UNVERIFIED')
        self.assertIsNone(self.d['operator'].one('SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s',(r['batch_id'],)))
    def test_end_to_end_missing_and_shared_outcome(self):
        x=self.setup_experiment(quick=True);r=self.registration(x);self.anchor(r)
        self.complete(x);f=self.final(x);self.anchor(f);self.wait_result(x)
        result=self.resolve(x,self.evidence(x));run=self.d['scorer'].score(x['experiment']);rows=self.d['scorer'].scores(run)
        self.assertEqual(len(rows),2);self.assertEqual({a['resolution_id'] for a in rows},{result})
        submitted=[a for a in rows if a['diagnostic_score'] is not None][0]
        self.assertAlmostEqual(float(submitted['diagnostic_score']),.09);self.assertIsNone(submitted['primary_score'])
        self.assertEqual(sum(a['state']=='MISSING_PREDICTION' for a in rows),1)
        self.assertEqual(run,self.d['scorer'].score(x['experiment']))
        recomputed=verify_export(self.d['operator'].export(x['experiment']),self.ca,self.profile)
        self.assertEqual(len(recomputed['independent_recalculation']),2)
    def test_two_person_confirm_and_stale_proposal(self):
        x=self.setup_experiment(quick=True);self.wait_result(x);ev=self.evidence(x)
        p=self.d['resolver'].propose(x['version'],'RESOLVED',ev,'Verified TEST publication and completeness')
        with psycopg.connect(DSN) as c:c.execute(sql.SQL('GRANT fi_resolution_reviewer TO {}').format(sql.Identifier(self.logins['resolver'])))
        try:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.d['resolver'].confirm(p,'Cannot approve own TEST proposal')
        finally:
            with psycopg.connect(DSN) as c:c.execute(sql.SQL('REVOKE fi_resolution_reviewer FROM {}').format(sql.Identifier(self.logins['resolver'])))
        with self.assertRaises(psycopg.errors.SerializationFailure):self.d['resolver'].propose(x['version'],'VOID',None,'Invalid stale TEST parent omitted')
        self.d['review'].confirm(p,'Independent TEST publication checked')
    def test_first_release_cannot_choose_later_revision(self):
        x=self.setup_experiment(quick=True,numeric=True);self.wait_result(x)
        first=self.evidence(x,'9',0);later=self.evidence(x,'11',1)
        with self.assertRaises(psycopg.errors.InvalidParameterValue):self.d['resolver'].propose(x['version'],'RESOLVED',later,'Cannot choose favourable later revision')
        self.resolve(x,first)
    def test_latest_policy_and_versioned_rescoring(self):
        x=self.setup_experiment(quick=True,numeric=True,policy='LATEST_AT_RESOLUTION_DEADLINE')
        self.registration(x);self.complete(x);self.complete(x,'worker2',[100000,100000,800000]);self.final(x);self.wait_result(x)
        first=self.evidence(x,'9',0)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.d['resolver'].propose(x['version'],'RESOLVED',first,'Cannot choose latest before deadline')
        later=self.evidence(x,'11',1);self.wait_result(x,latest=True);p=self.resolve(x,later)
        run=self.d['scorer'].score(x['experiment']);rows=self.d['scorer'].scores(run)
        for row in rows:
            probs=[200000,300000,500000] if row['entry_key']=='model-0' else [100000,100000,800000]
            self.assertAlmostEqual(float(row['diagnostic_score']),score(x['spec'],probs,'11')['primary'])
        self.resolve(x,None,'DISPUTED',p);new=self.d['scorer'].score(x['experiment']);self.assertNotEqual(run,new)
        self.assertEqual({a['state'] for a in self.d['scorer'].scores(new)},{'DISPUTED'})
        self.assertEqual(self.d['scorer'].scores(run),rows)
    def test_cannot_resolve_before_observation_window(self):
        x=self.setup_experiment()
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.evidence(x)
    def test_source_collection_permissions_and_no_false_vintage(self):
        x=self.setup_experiment();endpoint='https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0'
        self.assertTrue(self.d['source'].one('SELECT fi.admit_bls_collection(%s,%s)',(x['permission'],endpoint)))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.d['source'].one('SELECT fi.admit_bls_collection(%s,%s)',(x['permission'],'https://localhost/internal'))
        body=dict(adapter='BLS_V1_EXACT_PERIOD',series='CUUR0000SA0',year=2026,period='M01',value_text=None,has_target=False,
                  document_sha256='a'*64,observed_at=datetime.now(timezone.utc).isoformat(),publication_time_verified=False,first_release_verified=False,requires_blind_review=True)
        self.d['source'].one('SELECT fi.store_bls_capture(%s,%s,%s)',(x['permission'],endpoint,Jsonb(body)))
        body['first_release_verified']=True
        with self.assertRaises(psycopg.errors.InvalidParameterValue):self.d['source'].one('SELECT fi.store_bls_capture(%s,%s,%s)',(x['permission'],endpoint,Jsonb(body)))
    def test_worker_gate_blocks_unanchored_batch(self):
        x=self.setup_experiment();self.registration(x)
        self.assertFalse(self.d['worker'].one('SELECT fi.worker_delivery_gate(%s)',(x['experiment'],))['ready'])
    def test_new_records_immutable(self):
        x=self.setup_experiment();r=self.registration(x)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.d['operator'].one('DELETE FROM fi.batch_records WHERE batch_id=%s RETURNING ordinal',(r['batch_id'],))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.admin('UPDATE fi.batch_seals SET subject_sha256=subject_sha256 WHERE batch_id=%s RETURNING batch_id',(r['batch_id'],))
    def test_no_public_helpers(self):
        self.assertFalse(self.admin("SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='fi' AND a.grantee=0 AND a.privilege_type='EXECUTE')"))
