"""Real PostgreSQL for Terra contract; only synthetic transport/TSA, no live API."""
import copy
import json
import time
import unittest
from unittest.mock import patch
import psycopg
import test_forecast_contract as fixture
import test_forecast_contract_v2 as base
from future_intelligence.providers.live import build_request, strict_json, terra_generation_profile
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.tasks.live_worker import run_once


class TerraGenerationDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): base.ForecastContractV2Tests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.ForecastContractV2Tests.tearDownClass.__func__(cls)
    call=base.ForecastContractV2Tests.call
    admin=base.ForecastContractV2Tests.admin
    question=base.ForecastContractV2Tests.question
    checks=base.ForecastContractV2Tests.checks
    confirm=base.ForecastContractV2Tests.confirm
    request=base.ForecastContractV2Tests.request
    anchor=base.ForecastContractV2Tests.anchor

    def make(self,mode='DIAGNOSTIC',edges=None,generation=True):
        original=fixture.entry
        def config(*args,**kwargs):
            cfg=original(*args,**kwargs);cfg['model_id']='gpt-6.1-terra'
            if generation:cfg['parameters']['generation']=terra_generation_profile()
            return cfg
        with patch.object(fixture,'entry',side_effect=config):
            return base.ForecastContractV2Tests.make(self,mode=mode,edges=edges)

    def test_binary_and_five_category_bodies_persist_exactly(self):
        for edges in (None,['3.0','3.5','4.0','4.5']):
            e,_,_=self.make(edges=edges);lease,ctx,body=self.request(e)
            self.assertTrue(self.er['worker'].prepare(lease,body))
            stored=self.admin('SELECT request_text FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],))
            self.assertEqual(stored,body)
            self.assertEqual(strict_json(stored)['reasoning'],{'effort':'medium','mode':'standard'})
            self.assertEqual(ctx['answer_contract']['category_count'],2 if edges is None else 5)

    def test_generation_tampering_blocked_before_call_marker(self):
        e,_,_=self.make();lease,_,body=self.request(e)
        for key,value in (('reasoning',{'effort':'high','mode':'standard'}),
                          ('reasoning',{'effort':'medium','mode':'pro'}),
                          ('service_tier','priority'),('temperature',0),('seed',1)):
            bad=strict_json(body);bad[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.er['worker'].prepare(lease,json.dumps(bad))
        for key in ('reasoning','service_tier'):
            bad=strict_json(body);del bad[key]
            with self.subTest(missing=key),self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.er['worker'].prepare(lease,json.dumps(bad))
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),0)
        self.assertTrue(self.er['worker'].prepare(lease,body))

    def test_legacy_terra_diagnostic_remains_unchanged_but_not_formal(self):
        e,_,_=self.make(generation=False);lease,_,body=self.request(e)
        self.assertNotIn('reasoning',strict_json(body));self.assertTrue(self.er['worker'].prepare(lease,body))
        pending,_,_=self.make(mode=None,generation=False)
        with self.assertRaisesRegex(psycopg.errors.InsufficientPrivilege,'FORMAL_REQUIRES_NATIVE_ENDPOINT'):
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (pending,'FORMAL',5,3600,'Synthetic rejection; explicit Terra config required'))
        self.assertEqual(self.admin('SELECT count(*) FROM fi.operation_policies WHERE experiment_id=%s',(pending,)),0)

    def test_config_mutation_not_hidden_by_valid_request(self):
        original=fixture.entry
        def bad_config(*args,**kwargs):
            cfg=original(*args,**kwargs);cfg['model_id']='gpt-6.1-terra'
            cfg['parameters']['generation']=terra_generation_profile()
            cfg['parameters']['generation']['reasoning']['effort']='high'
            return cfg
        with patch.object(fixture,'entry',side_effect=bad_config):
            e,_,_=base.ForecastContractV2Tests.make(self,mode=None)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (e,'FORMAL',5,3600,'Synthetic invalid profile'))

    def test_fixed_terra_synthetic_formal_retains_both_time_gates(self):
        e,v,p=self.make(mode='FORMAL',edges=['3.0','3.5','4.0','4.5'])
        self.confirm(e,v,self.checks(e,v,p))
        operator=DeliveryRepository(self.dsns['operator'])
        reg=operator.capture(e,'REGISTRATION',base.previous.PLAN,self.profile_json);operator.seal(reg)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.er['worker'].claim(e,'LIVE')
        self.anchor(reg)
        class Transport:
            def send(self,provider,model,body,*args,**kwargs):
                assert provider=='openai' and model=='gpt-6.1-terra'
                assert strict_json(body)['reasoning']=={'effort':'medium','mode':'standard'}
                return fixture.wire([200000]*5)
        result=run_once(self.er['worker'],e,transport=Transport(),environ={'OPENAI_API_KEY':'synthetic'})
        self.assertEqual(result['status'],'OK');self.assertIsNone(result['actual_cost'])
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',(result['attempt_id'],)),'HELD_UNKNOWN')
        forecast=operator.capture(e,'FORECAST',base.previous.PLAN,self.profile_json);operator.seal(forecast)
        self.anchor(forecast)
        with self.assertRaises(psycopg.Error):self.er['worker'].claim(e,'LIVE')

    def test_helper_not_public_and_diagnostic_not_promoted(self):
        from psycopg.types.json import Jsonb
        e,_,_=self.make()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker','SELECT fi.valid_terra_generation(%s)',(Jsonb({}),))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (e,'FORMAL',5,3600,'Cannot promote existing diagnostic'))
