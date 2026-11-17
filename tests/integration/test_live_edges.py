"""Forward-regression contracts on real PostgreSQL; synthetic model observations."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
import psycopg
import test_live_evidence as base
from test_live_evidence import fixture


class LiveContractEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): base.LiveEvidenceTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.LiveEvidenceTests.tearDownClass.__func__(cls)
    call=base.LiveEvidenceTests.call
    admin=base.LiveEvidenceTests.admin
    question=base.LiveEvidenceTests.question
    state=base.LiveEvidenceTests.state
    make=base.LiveEvidenceTests.make
    claim=base.LiveEvidenceTests.claim
    prepare=base.LiveEvidenceTests.prepare
    observation=base.LiveEvidenceTests.observation

    def test_nested_output_contract_rejects_mutation(self):
        for provider in ('openai','anthropic','google'):
            e,_,_,_=self.make(provider); lease=self.claim(e)
            body=fixture.strict_json(fixture.build_request(lease['entry'],self.er['worker'].context(lease)))
            if provider=='openai': body['text']['format']['schema']['additionalProperties']=True
            elif provider=='anthropic': body['output_config']['format']['schema']['additionalProperties']=True
            else: body['generationConfig']['temperature']=0.9
            with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].prepare(lease,json.dumps(body))
            self.assertIsNone(self.admin('SELECT call_started_at FROM fi.task_attempts WHERE id=%s',(lease['attempt_id'],)))

    def test_duplicate_body_key_rejected_and_marker_rolled_back(self):
        e,_,_,_=self.make(); lease=self.claim(e)
        good=fixture.build_request(lease['entry'],self.er['worker'].context(lease))
        bad=good[:-1]+',"model":"fixture-model-not-live"}'
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].prepare(lease,bad)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),0)
        self.assertTrue(self.er['worker'].prepare(lease,good))

    def test_duplicate_inner_input_key_rejected(self):
        e,_,_,_=self.make(); lease=self.claim(e); ctx=self.er['worker'].context(lease)
        body=fixture.strict_json(fixture.build_request(lease['entry'],ctx))
        body['input']=body['input'][:-1]+',"protocol":"fi.model-input.v1"}'
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].prepare(lease,json.dumps(body))

    def test_bad_probabilities_rejected_before_persistence(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e)
        for probs in ([1,1],[True,999999],[-1,1000001],[1000000]):
            obs=self.observation(); obs['output']['probabilities_ppm']=probs
            with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].observe(lease,obs)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_observations WHERE attempt_id=%s',(lease['attempt_id'],)),0)

    def test_duplicate_citation_rejected_before_persistence(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); obs=self.observation()
        obs['output']['evidence_ids']=['e1','e1']
        with self.assertRaises(psycopg.errors.InvalidParameterValue): self.er['worker'].observe(lease,obs)

    def test_peer_cannot_read_or_observe_foreign_attempt(self):
        e,_,_,_=self.make(models=2); lease,_=self.prepare(e)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.er['worker2'].context(lease)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): self.er['worker2'].observe(lease,self.observation())

    def test_concurrent_prepare_yields_single_send_permission(self):
        e,_,_,_=self.make(); lease=self.claim(e)
        body=fixture.build_request(lease['entry'],self.er['worker'].context(lease))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.er['worker'].prepare(lease,body),range(2)))
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),1)

    def test_finish_replay_not_duplicate_billing(self):
        e,_,_,_=self.make(); lease,_=self.prepare(e); obs=self.observation(); self.er['worker'].observe(lease,obs)
        args=(lease,obs['output']['probabilities_ppm'],obs['response_sha256'],None)
        a=self.er['worker'].succeed(*args); b=self.er['worker'].succeed(*args)
        self.assertEqual(a,b)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_submissions WHERE task_id=%s',(lease['task_id'],)),1)
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',(lease['attempt_id'],)),'HELD_UNKNOWN')
