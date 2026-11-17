"""Additional admission and blind-review edge cases using real PostgreSQL."""
import json
import hashlib
from datetime import datetime,timezone
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient
from test_delivery_pipeline import DeliveryPipelineTests, PLAN, DSN
from tsa_fixture import timestamp
from future_intelligence.delivery.timestamps import verify_reply
from future_intelligence.delivery.workbench import create_app

# Reuse fixture helpers but do not repeat the parent's already-registered tests.
class DeliveryEdgeTests(DeliveryPipelineTests):
    def test_report_requires_explicit_uncertainty(self):
        x=self.setup_experiment();b=self.registration(x);rid,q=self.d['operator'].new_anchor(b['batch_id'])
        reply=timestamp(self.tsa,q);self.d['operator'].receive(rid,reply)
        result=verify_reply(b['subject_sha256'],q,reply,self.ca,self.profile,x['spec']['forecast_deadline'])
        bad=dict(result);del bad['accuracy_us']
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.d['verifier'].one('SELECT fi.attest_anchor(%s,%s)',(rid,Jsonb(bad)))
        bad=dict(result);bad['upper_time']=bad['gen_time']
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.d['verifier'].one('SELECT fi.attest_anchor(%s,%s)',(rid,Jsonb(bad)))
        self.d['verifier'].verify_anchor(rid,self.ca,self.profile)

    def test_verifier_login_not_operator(self):
        x=self.setup_experiment();b=self.registration(x);rid,q=self.d['operator'].new_anchor(b['batch_id'])
        reply=timestamp(self.tsa,q);self.d['operator'].receive(rid,reply)
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL('GRANT fi_verifier TO {}').format(sql.Identifier(self.logins['operator'])))
        try:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.d['operator'].verify_anchor(rid,self.ca,self.profile)
        finally:
            with psycopg.connect(DSN) as c:
                c.execute(sql.SQL('REVOKE fi_verifier FROM {}').format(sql.Identifier(self.logins['operator'])))

    def test_duplicate_comparison_family_rejected_in_db(self):
        x=self.setup_experiment()
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.d['operator'].capture(x['experiment'],'REGISTRATION',
                {**PLAN,'comparisons':[['model-0','model-1'],['model-1','model-0']]},self.profile_json)

    def test_late_vintage_invalidates_current_result_not_history(self):
        x=self.setup_experiment(quick=True,numeric=True)
        self.registration(x);self.complete(x);self.final(x);self.wait_result(x)
        revision=self.evidence(x,'11',1);self.resolve(x,revision)
        before=self.d['scorer'].score(x['experiment'])
        self.evidence(x,'9',0)
        current=self.d['resolver'].one('SELECT status FROM fi.outcome_catalog WHERE version_id=%s',(x['version'],))
        self.assertEqual(current,'STALE_EVIDENCE')
        after=self.d['scorer'].score(x['experiment']);self.assertNotEqual(before,after)
        self.assertTrue(any(r['diagnostic_score'] is not None for r in self.d['scorer'].scores(before)))
        self.assertTrue(all(r['diagnostic_score'] is None for r in self.d['scorer'].scores(after)))

    def test_repeatable_read_cannot_confirm(self):
        x=self.setup_experiment(quick=True);self.wait_result(x)
        ev=self.evidence(x);p=self.d['resolver'].propose(x['version'],'RESOLVED',ev,'Checked synthetic official release')
        with psycopg.connect(self.dsns['review']) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            with self.assertRaises(psycopg.errors.InvalidTransactionState):
                c.execute('SELECT fi.confirm_resolution(%s,%s)',(p,'Checked synthetic independent source'))

    def test_real_db_workbench_role_routes(self):
        accounts={role:(char*40,self.d[dbrole]) for role,char,dbrole in
                  [('dashboard','d','dashboard'),('resolver','r','resolver'),('reviewer','v','review'),('scorer','s','scorer')]}
        client=TestClient(create_app(accounts))
        for path in ('experiments','batches','outcomes','runs'):
            response=client.get('/api/v1/'+path,headers={'Authorization':'Bearer '+'d'*40})
            self.assertEqual(response.status_code,200,(path,response.text))
        self.assertEqual(client.get('/api/v1/batches',headers={'Authorization':'Bearer '+'r'*40}).status_code,403)
        self.assertEqual(client.get('/api/v1/outcomes',headers={'Authorization':'Bearer '+'v'*40}).status_code,200)

# Old tests still run in their original module; avoid duplicate test counts here.
for _name in tuple(DeliveryPipelineTests.__dict__):
    if _name.startswith('test_'):
        setattr(DeliveryEdgeTests,_name,None)
del DeliveryPipelineTests
