import unittest
from fastapi.testclient import TestClient
from future_intelligence.delivery.workbench import create_app


class FakeRepository:
    def overview(self):return [{'id':'fixture','name':'<script>not HTML</script>','unknown_cost':None}]
    def batches(self):return []
    def outcomes(self):return [{'status':'PENDING'}]
    def score_runs(self):return []
    def score_summary(self,r):return [{'registered_tasks':3,'timed_live_scored':0}]
    def propose(self,*args):return 'fixture-proposal'
    def confirm(self,*args):pass
    def score(self,*args):return 'fixture-run'


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.accounts={r:(r[0]*40,FakeRepository()) for r in ('dashboard','resolver','scorer')}
        self.accounts['reviewer']=('v'*40,FakeRepository())
        self.client=TestClient(create_app(self.accounts))
    def h(self,r):return {'Authorization':'Bearer '+self.accounts[r][0]}
    def test_public_shell_no_research(self):
        r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn('研究工作台',r.text)
        self.assertEqual(self.client.get('/api/v1/experiments').status_code,401)
    def test_no_token_in_url(self):
        self.assertEqual(self.client.get('/api/v1/experiments?token='+'d'*40).status_code,401)
    def test_dashboard(self):
        r=self.client.get('/api/v1/experiments',headers=self.h('dashboard'));self.assertEqual(r.status_code,200)
        self.assertIn('no-store',r.headers['cache-control']);self.assertIn('frame-ancestors',r.headers['content-security-policy'])
    def test_blind_resolver_cannot_read_scores(self):
        for path in ('experiments','batches','runs'):
            self.assertEqual(self.client.get('/api/v1/'+path,headers=self.h('resolver')).status_code,403)
    def test_blind_reviewer_can_read_outcomes(self):
        self.assertEqual(self.client.get('/api/v1/outcomes',headers=self.h('reviewer')).status_code,200)
    def test_no_public_model_trigger(self):
        self.assertEqual(self.client.post('/api/v1/run-model',json={},headers=self.h('dashboard')).status_code,404)
    def test_roles_on_write(self):
        body=dict(version='x',kind='VOID',evidence_id=None,reason='Explicit fixture reason',parent=None,fallback_reason=None)
        self.assertEqual(self.client.post('/api/v1/resolutions',json=body,headers=self.h('dashboard')).status_code,403)
        self.assertEqual(self.client.post('/api/v1/resolutions',json=body,headers=self.h('resolver')).status_code,200)
    def test_secret_errors_redacted(self):
        def broken():raise RuntimeError('password=DO_NOT_DISCLOSE')
        self.accounts['dashboard'][1].overview=broken
        r=self.client.get('/api/v1/experiments',headers=self.h('dashboard'))
        self.assertEqual(r.status_code,409);self.assertNotIn('DO_NOT_DISCLOSE',r.text)
    def test_duplicate_weak_tokens_denied(self):
        with self.assertRaises(ValueError):create_app({'dashboard':('weak',FakeRepository())})
        with self.assertRaises(ValueError):create_app({'dashboard':('x'*40,FakeRepository()),'resolver':('x'*40,FakeRepository())})
    def test_host_protection(self):
        self.assertEqual(self.client.get('/',headers={'Host':'attacker.invalid'}).status_code,400)
    def test_oversized_body(self):
        self.assertEqual(self.client.post('/api/v1/resolutions',content='x'*20000,headers=self.h('resolver')).status_code,413)
    def test_static_dom_not_innerhtml(self):
        script=self.client.get('/ui.js').text
        self.assertNotIn('innerHTML',script);self.assertIn('textContent',script)
    def test_false_content_length_does_not_bypass_limit(self):
        headers={**self.h('resolver'),'content-length':'1'}
        self.assertEqual(self.client.post('/api/v1/resolutions',content='x'*20000,headers=headers).status_code,413)
    def test_mismatched_content_length_rejected(self):
        headers={**self.h('resolver'),'content-length':'1'}
        self.assertEqual(self.client.post('/api/v1/resolutions',content='{}',headers=headers).status_code,400)
