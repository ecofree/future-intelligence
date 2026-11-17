"""Real PostgreSQL regression; model responses and TSA certificates are synthetic."""
import copy
import hashlib
import json
from datetime import datetime, timezone
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import test_operational_admission as previous
import test_forecast_contract as fixture
from test_question_spec import example_spec
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.providers.live import build_request, strict_json, parse_response
from future_intelligence.questions.spec import answer_contract
from future_intelligence.tasks.live_worker import run_once
from tsa_fixture import timestamp


class ForecastContractV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): previous.OperationalAdmissionTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): previous.OperationalAdmissionTests.tearDownClass.__func__(cls)
    call=previous.OperationalAdmissionTests.call
    admin=previous.OperationalAdmissionTests.admin
    question=previous.OperationalAdmissionTests.question
    checks=previous.OperationalAdmissionTests.checks
    confirm=previous.OperationalAdmissionTests.confirm

    def make(self,provider='openai',edges=None,legacy=False,mode='DIAGNOSTIC',extra=None):
        # Reuse established role separation and source permission helpers.
        _,pid=self.question(reviewed=False)
        sid=self.admin('SELECT source_id FROM fi.source_permissions WHERE id=%s',(pid,))
        spec=example_spec()
        if edges is not None: spec.update(question_type='binned_numeric',bin_edges=edges,unit='fixture')
        v=self.author.create_version('contract-v2-'+uuid4().hex,spec,[str(pid)])
        self.reviewer.review(v,'Synthetic contract review');self.author.freeze(v)
        cfg=fixture.entry(provider,legacy)
        if extra: cfg['parameters'].update(extra)
        plan=previous.previous.previous.experiment_spec(run_kind='LIVE',budget_microusd=100000000,
                    attempt_cap_microusd=1000000,lease_seconds=120)
        e=self.r['operator'].register('contract-v2-'+uuid4().hex,[v],[cfg],plan)
        if mode:
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (e,mode,5,3600,'Synthetic contract regression; no real API or invoice'))
        self.r['operator'].bind_worker(e,'model-0',self.logins['worker'])
        packets=[{'question_version_id':v,'items':[{'id':'e1','source_id':str(sid),'permission_id':str(pid),
                    'title':'SYNTHETIC CONTRACT','text':'Synthetic test evidence, not real research.'}]}]
        self.er['operator'].seal(e,packets)
        return e,v,pid

    def request(self,e):
        lease=self.er['worker'].claim(e,'LIVE')
        ctx=self.er['worker'].context(lease)
        return lease,ctx,build_request(lease['entry'],ctx)

    def anchor(self,bid):
        operator=DeliveryRepository(self.dsns['operator'])
        rid,q=operator.new_anchor(bid)
        operator.receive(rid,timestamp(self.tsa,q))
        report=DeliveryRepository(self.dsns['verifier']).verify_anchor(rid,self.ca,self.profile)
        time.sleep(max(0,(datetime.fromisoformat(report['upper_time'])-datetime.now(timezone.utc)).total_seconds())+.02)
        self.assertEqual(report['status'],'VERIFIED')
        return report

    def test_sql_python_contract_exact_agreement(self):
        for edges in (None,['0'],['-1e-20','0','1.5','10'],[str(i) for i in range(100)]):
            spec=example_spec()
            if edges is not None: spec.update(question_type='binned_numeric',bin_edges=edges)
            got=self.admin('SELECT fi.answer_contract_v2(%s)',(Jsonb(spec),))
            self.assertEqual(got,answer_contract(spec))

    def test_four_boundaries_five_probabilities_three_requests(self):
        for provider in ('openai','anthropic','google'):
            e,_,_=self.make(provider,edges=['1','2','3','4'])
            lease,ctx,body=self.request(e)
            self.assertEqual(ctx['protocol'],'fi.model-input.v2')
            self.assertEqual(ctx['answer_contract']['category_count'],5)
            self.assertTrue(self.er['worker'].prepare(lease,body))
            obs=parse_response(provider,fixture.wire([200000]*5,provider),5,{'e1'})
            self.er['worker'].observe(lease,obs)
            self.er['worker'].succeed(lease,obs['output']['probabilities_ppm'],obs['response_sha256'],None)
            self.assertEqual(self.admin('SELECT probabilities_ppm FROM fi.task_submissions WHERE task_id=%s',(lease['task_id'],)),[200000]*5)
            self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',(lease['attempt_id'],)),'HELD_UNKNOWN')

    def test_legacy_input_and_records_unchanged(self):
        e,_,_=self.make(legacy=True)
        lease,ctx,body=self.request(e)
        self.assertEqual(ctx['protocol'],'fi.model-input.v1');self.assertNotIn('answer_contract',ctx)
        self.assertTrue(self.er['worker'].prepare(lease,body))
        before=self.admin('SELECT request_sha256 FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],))
        other,_,_=self.make(edges=['1','2','3','4']);newlease,_,newbody=self.request(other)
        self.assertTrue(self.er['worker'].prepare(newlease,newbody))
        self.assertEqual(before,self.admin('SELECT request_sha256 FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)))
        self.assertEqual(before,hashlib.sha256(body.encode()).hexdigest())

    def test_sql_rejects_wrong_or_removed_native_length(self):
        e,_,_=self.make(edges=['1','2','3','4']);lease,_,body=self.request(e)
        for key,value in [('minItems',4),('maxItems',6),('maxItems',None)]:
            bad=strict_json(body);a=bad['text']['format']['schema']['properties']['probabilities_ppm']
            if value is None: del a[key]
            else:a[key]=value
            with self.assertRaises(psycopg.errors.InvalidParameterValue):self.er['worker'].prepare(lease,json.dumps(bad))
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),0)
        self.assertTrue(self.er['worker'].prepare(lease,body))

    def test_sql_rejects_tampered_category_order_and_boundaries(self):
        e,_,_=self.make(edges=['1','2','3','4']);lease,_,body=self.request(e)
        for change in ('order','boundary','count','protocol'):
            bad=strict_json(body);ctx=strict_json(bad['input'])
            if change=='order':ctx['answer_contract']['categories'].reverse()
            elif change=='boundary':ctx['answer_contract']['categories'][1]['lower_inclusive']=False
            elif change=='count':ctx['answer_contract']['category_count']=4
            else:ctx['protocol']='fi.model-input.v1'
            bad['input']=json.dumps(ctx)
            with self.assertRaises(psycopg.errors.InvalidParameterValue):self.er['worker'].prepare(lease,json.dumps(bad))
        self.assertTrue(self.er['worker'].prepare(lease,body))

    def test_wrong_probability_count_stays_failed_and_in_denominator(self):
        e,_,_=self.make(edges=['1','2','3','4'])
        class Transport:
            def send(self,*args): return fixture.wire([250000]*4)
        result=run_once(self.er['worker'],e,transport=Transport(),environ={'OPENAI_API_KEY':'synthetic'})
        self.assertEqual(result['status'],'INVALID_OUTPUT')
        self.assertEqual(self.r['operator'].summary(e)['total_tasks'],1)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_submissions WHERE task_id=%s',(result['task_id'],)),0)
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',(result['attempt_id'],)),'HELD_UNKNOWN')

    def test_extra_endpoint_config_rejected_at_formal_policy(self):
        for key in ('base_url','endpoint_profile','protocol','trust_level'):
            e,_,_=self.make(mode=None,extra={key:'synthetic-relay'})
            with self.subTest(key=key),self.assertRaisesRegex(psycopg.errors.InsufficientPrivilege,'FORMAL_REQUIRES_NATIVE_ENDPOINT'):
                self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                          (e,'FORMAL',5,3600,'Do not mislabel a relay as native official execution'))
            self.assertEqual(self.admin('SELECT count(*) FROM fi.operation_policies WHERE experiment_id=%s',(e,)),0)

    def test_diagnostic_configuration_not_promoted(self):
        e,_,_=self.make(extra={'base_url':'https://example.invalid/relay'})
        self.assertEqual(self.admin('SELECT mode FROM fi.operation_policies WHERE experiment_id=%s',(e,)),'DIAGNOSTIC')
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',(e,'FORMAL',5,3600,'Cannot upgrade old diagnostic experiment'))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            DeliveryRepository(self.dsns['operator']).capture(e,'REGISTRATION',previous.PLAN,self.profile_json)

    def test_helper_privileges_not_public(self):
        for function in ('fi.answer_contract_v2(%s)','fi.native_entries_allowed(%s)'):
            arg=Jsonb(example_spec()) if 'answer_contract' in function else str(uuid4())
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.call('worker','SELECT '+function,(arg,))

    def test_v2_formal_through_existing_gate_and_both_batches(self):
        e,v,p=self.make(edges=['1','2','3','4'],mode='FORMAL')
        self.confirm(e,v,self.checks(e,v,p))
        operator=DeliveryRepository(self.dsns['operator'])
        reg=operator.capture(e,'REGISTRATION',previous.PLAN,self.profile_json);operator.seal(reg)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.er['worker'].claim(e,'LIVE')
        self.anchor(reg)
        class Transport:
            def send(self,*args):return fixture.wire([200000]*5)
        result=run_once(self.er['worker'],e,transport=Transport(),environ={'OPENAI_API_KEY':'synthetic'})
        self.assertEqual(result['status'],'OK')
        forecast=operator.capture(e,'FORECAST',previous.PLAN,self.profile_json);operator.seal(forecast)
        self.anchor(forecast)
        self.assertEqual(self.r['operator'].summary(e)['total_tasks'],1)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.commitment_batches WHERE experiment_id=%s',(e,)),2)
        with self.assertRaises(psycopg.Error):self.er['worker'].claim(e,'LIVE')
