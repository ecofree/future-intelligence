"""Real PostgreSQL, synthetic OpenAI-compatible transport only; no external calls."""
import copy
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_compatible_providers as compat
import test_live_evidence as live
import test_task_ledger as previous
from future_intelligence.providers import compatible
from future_intelligence.providers.live import build_request, parse_response, strict_json
from future_intelligence.tasks.live_worker import run_once


class CompatibleLiveTests(live.LiveEvidenceTests):
    """Reuses the LIVE evidence harness; only compatible-specific cases are added."""

    def make_compatible(self, endpoint=None, model='deepseek-chat'):
        vid,pid=self.question()
        sid=self.admin('SELECT source_id FROM fi.source_permissions WHERE id=%s',(pid,))
        cfg=compat.entry(endpoint,model)
        spec=previous.experiment_spec(run_kind='LIVE',budget_microusd=100000000,attempt_cap_microusd=1000000,lease_seconds=120)
        e=self.r['operator'].register('compat-'+uuid4().hex,[vid],[cfg],spec)
        self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                  (e,'DIAGNOSTIC',5,3600,'Synthetic API contract test; not a prospective experiment'))
        self.r['operator'].bind_worker(e,cfg['entry_key'],self.logins['worker'])
        self.er['operator'].seal(e,[{'question_version_id':vid,'items':[{'id':'e1','source_id':str(sid),'permission_id':str(pid),
            'title':'SYNTHETIC TEST INPUT','text':'Synthetic evidence. Not a real-world release.'}]}])
        return e

    def test_registration_rejects_invalid_profiles(self):
        vid,_=self.question()
        spec=previous.experiment_spec(run_kind='LIVE',budget_microusd=100000000,attempt_cap_microusd=1000000,lease_seconds=120)
        bad=[compat.profile(base_url='http://plain.example.com'),compat.profile(api_key_env='FI_WORKER_DATABASE_URL'),
             compat.profile(response_format='text'),{**compat.profile(),'api_key':'x'},None]
        for endpoint in bad:
            cfg=compat.entry(); cfg['parameters']['endpoint']=endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.r['operator'].register('compat-bad-'+uuid4().hex,[vid],[cfg],spec)

    def test_manifest_freezes_endpoint(self):
        e=self.make_compatible()
        manifest=json.loads(self.admin('SELECT manifest_text FROM fi.experiments WHERE id=%s',(e,)))
        self.assertEqual(manifest['entries'][0]['parameters']['endpoint'],compat.profile())

    def test_request_shape_enforced_by_database(self):
        for endpoint in (compatible.PRESETS['deepseek'],compatible.PRESETS['openrouter']):
            e=self.make_compatible(endpoint); lease=self.claim(e); ctx=self.er['worker'].context(lease)
            good=strict_json(build_request(lease['entry'],ctx))
            other={'type':'json_object'} if endpoint['response_format']=='json_schema' else {'type':'json_schema','json_schema':{}}
            mutations=[('model','different'),('stream',True),('max_tokens',999),('response_format',other),
                       ('messages',good['messages'][:1]),('messages',[good['messages'][1],good['messages'][0]])]
            for key,value in mutations:
                bad=copy.deepcopy(good); bad[key]=value
                with self.subTest(endpoint=endpoint['name'],key=key), self.assertRaises(psycopg.errors.InvalidParameterValue):
                    self.er['worker'].prepare(lease,json.dumps(bad))
            bad=copy.deepcopy(good); bad['messages'][0]['content']='rewritten prompt'
            with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].prepare(lease,json.dumps(bad))
            bad=copy.deepcopy(good); bad['tools']=[]
            with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].prepare(lease,json.dumps(bad))
            self.assertTrue(self.er['worker'].prepare(lease,json.dumps(good)))

    def test_compatible_runner_end_to_end(self):
        e=self.make_compatible(); transport=compat.FakeWire(compat.response())
        result=run_once(self.er['worker'],e,transport,{'DEEPSEEK_API_KEY':'synthetic-test-key-not-real'})
        self.assertEqual(result['status'],'OK'); self.assertEqual(len(transport.calls),1)
        self.assertIsNone(result['actual_cost']); self.assertEqual(self.state(result['task_id']),'SUCCEEDED')
        row=self.admin('SELECT provider FROM fi.provider_requests WHERE attempt_id=%s',(result['attempt_id'],))
        self.assertEqual(row,'openai_compatible')

    def test_unregistered_endpoint_blocked_no_dispatch(self):
        relay={'name':'relay','base_url':'https://relay.example.com/v1','api_key_env':'RELAY_API_KEY','response_format':'json_object'}
        e=self.make_compatible(relay,'gpt-4o-mini'); transport=compat.FakeWire(compat.response())
        out=run_once(self.er['worker'],e,transport,{'RELAY_API_KEY':'synthetic'})
        self.assertEqual(out['reason'],'ENDPOINT_NOT_REGISTERED'); self.assertEqual(transport.calls,[])
        self.assertEqual(self.state(out['task_id']),'FAILED')


# Do not re-run the inherited LIVE evidence suite twice.
for _name in [n for n in dir(live.LiveEvidenceTests) if n.startswith('test_')]:
    if _name not in CompatibleLiveTests.__dict__:
        setattr(CompatibleLiveTests,_name,None)

if __name__=='__main__':
    unittest.main()
