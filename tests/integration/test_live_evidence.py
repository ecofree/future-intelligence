"""Real PostgreSQL, synthetic provider fixtures only. No external calls or invoices."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_live_providers as fixture
import test_task_ledger as previous
from future_intelligence.evidence.repository import EvidenceRepository
from future_intelligence.tasks.live_worker import run_once


class LiveEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous.TaskLedgerTests.setUpClass.__func__(cls)
        cls.er={k:EvidenceRepository(dsn) for k,dsn in cls.dsns.items()}
    @classmethod
    def tearDownClass(cls): previous.TaskLedgerTests.tearDownClass.__func__(cls)
    call=previous.TaskLedgerTests.call
    admin=previous.TaskLedgerTests.admin
    question=previous.TaskLedgerTests.question
    state=previous.TaskLedgerTests.state

    def make(self, provider='openai', models=1, sealed=True, operation_mode='DIAGNOSTIC', **changes):
        vid,pid=self.question()
        sid=self.admin('SELECT source_id FROM fi.source_permissions WHERE id=%s',(pid,))
        cfgs=[dict(fixture.entry(provider),entry_key=f'model-{i}') for i in range(models)]
        spec=previous.experiment_spec(run_kind='LIVE',budget_microusd=100000000,
             attempt_cap_microusd=1000000,lease_seconds=120,**changes)
        e=self.r['operator'].register('live-test-'+uuid4().hex,[vid],cfgs,spec)
        # Explicit API-contract diagnostics, never formal predictive evidence.
        if operation_mode is not None:
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (e,operation_mode,5,3600,'Synthetic API contract test; not a prospective experiment'))
        self.r['operator'].bind_worker(e,'model-0',self.logins['worker'])
        self.r['operator'].bind_worker(e,'model-0' if models==1 else 'model-1',self.logins['worker2'])
        packets=[{'question_version_id':vid,'items':[{'id':'e1','source_id':str(sid),'permission_id':str(pid),
                  'title':'SYNTHETIC TEST INPUT','text':'Synthetic evidence. Not a real-world release.'}]}]
        if sealed: self.er['operator'].seal(e,packets)
        return e,vid,pid,packets

    def claim(self,e,role='worker'):
        return self.er[role].claim(e,'LIVE')

    def prepare(self,e,role='worker'):
        lease=self.claim(e,role); ctx=self.er[role].context(lease)
        body=fixture.build_request(lease['entry'],ctx)
        self.assertTrue(self.er[role].prepare(lease,body))
        return lease,body

    def observation(self,provider='openai'):
        return fixture.parse_response(provider,fixture.response(provider),2,{'e1'})

    def test_live_cannot_claim_without_seal(self):
        e,_,_,_=self.make(sealed=False)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.claim(e)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE experiment_id=%s',(e,)),0)

    def test_seal_idempotent_and_content_change_denied(self):
        e,_,_,p=self.make()
        a=self.er['operator'].seal(e,p); self.assertEqual(a,self.er['operator'].seal(e,p))
        p[0]['items'][0]['text']='changed'
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.er['operator'].seal(e,p)

    def test_incomplete_batch_atomic(self):
        e,_,_,_=self.make(sealed=False)
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['operator'].seal(e,[])
        self.assertEqual(self.admin('SELECT count(*) FROM fi.evidence_packets WHERE experiment_id=%s',(e,)),0)

    def test_extra_and_duplicate_evidence_denied(self):
        e,_,_,p=self.make(sealed=False)
        for bad in ('observed_at','duplicate'):
            v=copy.deepcopy(p)
            if bad=='duplicate': v[0]['items'].append(v[0]['items'][0])
            else: v[0][bad]='1900-01-01T00:00:00Z'
            with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['operator'].seal(e,v)

    def test_foreign_source_denied(self):
        e,_,_,p=self.make(sealed=False); p[0]['items'][0]['source_id']=str(uuid4())
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.er['operator'].seal(e,p)

    def test_revoked_source_at_seal(self):
        e,_,pid,p=self.make(sealed=False)
        self.call('source','SELECT fi.revoke_source_permission(%s,%s)',(pid,'fixture revoke'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.er['operator'].seal(e,p)

    def test_frozen_packet_equal_for_models(self):
        e,_,_,_=self.make(models=2)
        a=self.er['worker'].context(self.claim(e)); b=self.er['worker2'].context(self.claim(e,'worker2'))
        self.assertEqual(a,b)
        self.assertIn('observed_at',a['evidence'])

    def test_worker_and_resolver_no_direct_read_or_write(self):
        e,_,_,_=self.make()
        for role in ('worker','resolver'):
            for table in ('provider_requests','provider_observations','evidence_packets'):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self.call(role,'SELECT count(*) FROM fi.'+table)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.er['worker'].seal(e,[])

    def test_legacy_call_gate_cannot_bypass_live(self):
        e,_,_,_=self.make(); lease=self.claim(e)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.er['worker'].start_call(lease)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker','SELECT fi.start_task_call_p002(%s,%s,%s)',self.er['worker']._identity(lease))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker','SELECT fi.claim_task_p002(%s,%s)',(e,'LIVE'))

    def test_prepare_validates_prompt_input_model_tools(self):
        e,_,_,_=self.make(); lease=self.claim(e); ctx=self.er['worker'].context(lease)
        good=fixture.strict_json(fixture.build_request(lease['entry'],ctx))
        for key,value in [('instructions','wrong'),('input','{}'),('model','different'),('tools',[{'type':'web_search'}]),('store',True),('max_output_tokens',999)]:
            bad=copy.deepcopy(good); bad[key]=value
            with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].prepare(lease,json.dumps(bad))
        self.assertTrue(self.er['worker'].prepare(lease,json.dumps(good)))

    def test_all_three_request_shapes(self):
        for provider in ('openai','anthropic','google'):
            e,_,_,_=self.make(provider); lease,body=self.prepare(e)
            row=self.admin('SELECT request_text FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],))
            self.assertEqual(row,body)
            self.assertFalse(self.er['worker'].prepare(lease,body))

    def test_permission_rechecked_at_dispatch(self):
        e,_,pid,_=self.make(); lease=self.claim(e); body=fixture.build_request(lease['entry'],self.er['worker'].context(lease))
        self.call('source','SELECT fi.revoke_source_permission(%s,%s)',(pid,'fixture revoke'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.er['worker'].prepare(lease,body)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),0)

    def test_observation_requires_registered_attempt(self):
        e,_,_,_=self.make(); lease=self.claim(e)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.er['worker'].observe(lease,self.observation())

    def test_succeed_requires_observation(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].succeed(lease,[300000,700000],'a'*64,None)

    def test_observation_idempotent_conflict_denied(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); obs=self.observation()
        digest=self.er['worker'].observe(lease,obs)
        self.assertEqual(digest,self.er['worker'].observe(lease,obs))
        obs['request_id']='changed'
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.er['worker'].observe(lease,obs)

    def test_mismatch_cannot_override_observed_probability(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); obs=self.observation(); self.er['worker'].observe(lease,obs)
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.er['worker'].succeed(lease,[400000,600000],obs['response_sha256'],None)
        self.er['worker'].succeed(lease,obs['output']['probabilities_ppm'],obs['response_sha256'],None)
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',(lease['attempt_id'],)),'HELD_UNKNOWN')

    def test_estimated_cost_cannot_be_settled_as_actual(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); obs=self.observation(); self.er['worker'].observe(lease,obs)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].succeed(lease,obs['output']['probabilities_ppm'],obs['response_sha256'],140)

    def test_unknown_citation_and_private_fields_denied(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e)
        obs=self.observation(); obs['output']['evidence_ids']=['fabricated']
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].observe(lease,obs)
        obs=self.observation(); obs['raw_reasoning']='not permitted'
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].observe(lease,obs)

    def test_late_observation_survives_but_cannot_score(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); obs=self.observation()
        # Admin fixture simulates already elapsed lease without waiting 120 seconds.
        self.admin("UPDATE fi.task_states SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s RETURNING task_id",(lease['task_id'],))
        self.er['worker'].observe(lease,obs)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].succeed(lease,obs['output']['probabilities_ppm'],obs['response_sha256'],None)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_observations WHERE attempt_id=%s',(lease['attempt_id'],)),1)

    def test_live_runner_three_synthetic_transports(self):
        for provider,key in [('openai','OPENAI_API_KEY'),('anthropic','ANTHROPIC_API_KEY'),('google','GEMINI_API_KEY')]:
            e,_,_,_=self.make(provider); transport=fixture.FakeWire(fixture.response(provider))
            result=run_once(self.er['worker'],e,transport,{key:'synthetic-test-key-not-real'})
            self.assertEqual(result['status'],'OK'); self.assertEqual(transport.calls,1)
            self.assertIsNone(result['actual_cost'])
            self.assertEqual(self.state(result['task_id']),'SUCCEEDED')

    def test_missing_key_recorded_no_dispatch(self):
        e,_,_,_=self.make(); transport=fixture.FakeWire()
        out=run_once(self.er['worker'],e,transport,{})
        self.assertEqual(out['status'],'BLOCKED'); self.assertEqual(transport.calls,0)
        self.assertEqual(self.state(out['task_id']),'FAILED')
        self.assertEqual(self.admin('SELECT actual_microusd FROM fi.budget_reservations WHERE attempt_id=%s',(out['attempt_id'],)),0)

    def test_timeout_stays_uncertain_no_retry(self):
        e,_,_,_=self.make(); transport=fixture.FakeWire(fixture.WireResponse(None,b'',error_code='TIMEOUT'))
        out=run_once(self.er['worker'],e,transport,{'OPENAI_API_KEY':'fixture-key'})
        self.assertEqual(out['status'],'TIMEOUT'); self.assertEqual(transport.calls,1)
        self.assertEqual(self.state(out['task_id']),'UNCERTAIN')
        self.assertIsNone(self.claim(e))

    def test_response_metadata_immutable(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); self.er['worker'].observe(lease,self.observation())
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('operator','DELETE FROM fi.provider_observations WHERE attempt_id=%s RETURNING attempt_id',(lease['attempt_id'],))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.admin('UPDATE fi.provider_requests SET request_text=request_text WHERE attempt_id=%s RETURNING attempt_id',(lease['attempt_id'],))

    def test_concurrent_seal_one_manifest(self):
        e,_,_,p=self.make(sealed=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(lambda _:self.er['operator'].seal(e,p),range(2)))
        self.assertEqual(values[0],values[1])
        self.assertEqual(self.admin('SELECT count(*) FROM fi.evidence_seals WHERE experiment_id=%s',(e,)),1)
