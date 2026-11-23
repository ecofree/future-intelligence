"""Real PostgreSQL for Terra contract; only synthetic transport/TSA, no live API."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import test_task_ledger as ledger_fixture
import test_forecast_contract as fixture
import test_forecast_contract_v2 as base
from future_intelligence.providers.live import build_request, encode, strict_json, terra_generation_profile, WireResponse
from test_input_count_worker import receipt_for_body
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.tasks.live_worker import run_once
from future_intelligence.db.migrate import migrate
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.evidence.repository import EvidenceRepository
from future_intelligence.tasks.repository import TaskRepository


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

    def setUp(self):
        # A replacement ledger is immutable and site-wide. Each example needs its own database.
        owner = ledger_fixture.DSN
        name = 'fi_series_test_' + uuid4().hex
        def database_command(statement):
            with psycopg.connect(owner, autocommit=True) as conn:
                conn.execute(sql.SQL(statement).format(sql.Identifier(name)))
        database_command('CREATE DATABASE {}')
        self.addCleanup(database_command, 'DROP DATABASE {}')
        dsn = make_conninfo(owner, dbname=name)
        migrate(dsn)
        self.enterContext(patch.object(ledger_fixture, 'DSN', dsn))
        self.dsns = {role: make_conninfo(value, dbname=name) for role,value in self.dsns.items()}
        self.er = {role: EvidenceRepository(value) for role,value in self.dsns.items()}
        self.r = {role: TaskRepository(value) for role,value in self.dsns.items()}
        self.author = QuestionRepository(self.dsns['author'])
        self.reviewer = QuestionRepository(self.dsns['reviewer'])

    def make(self,mode='DIAGNOSTIC',edges=None,generation=True):
        original=fixture.entry
        def config(*args,**kwargs):
            cfg=original(*args,**kwargs);cfg['model_id']='gpt-6.1-terra'
            if generation:cfg['parameters']['generation']=terra_generation_profile()
            return cfg
        with patch.object(fixture,'entry',side_effect=config):
            return base.ForecastContractV2Tests.make(self,mode=mode,edges=edges,attempt_cap=70000)

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

    def open_test_ledger(self, calls=2, opening=840000):
        self.admin("SELECT fi.open_series_budget(%s,'2020-01-01T00:00:00Z',%s,1000000,%s,70000)",
                   ('a'*64,Jsonb({'synthetic_historical_hold':opening}),calls))

    def record_test_cutover(self):
        self.admin('INSERT INTO fi.series_trust(singleton,profile,source_review_sha256) VALUES(true,%s,%s) RETURNING singleton',
                   (Jsonb(self.profile_json),'d'*64))
        self.admin("""INSERT INTO fi.series_cutover(singleton,fence_sha256,billing_sha256,covered_until,additional_held_microusd)
                      VALUES(true,%s,%s,clock_timestamp(),0) RETURNING singleton""",('b'*64,'c'*64))

    def admitted_terra(self, authorize=True):
        e,v,p=self.make(mode='FORMAL',edges=['3.0','3.5','4.0','4.5'])
        self.confirm(e,v,self.checks(e,v,p))
        operator=DeliveryRepository(self.dsns['operator'])
        reg=operator.capture(e,'REGISTRATION',base.previous.PLAN,self.profile_json);operator.seal(reg)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.er['worker'].claim(e,'LIVE')
        self.anchor(reg)
        task = self.admin('SELECT id FROM fi.expected_tasks WHERE experiment_id=%s',(e,))
        config = self.admin('SELECT config FROM fi.experiment_entries WHERE experiment_id=%s',(e,))
        ctx = self.admin('SELECT fi.live_input(%s)', (task,))
        if authorize:
            self.open_test_ledger()
            self.record_test_cutover()
            self.admin('INSERT INTO fi.series_permits(task_id) VALUES(%s) RETURNING task_id',(task,))
        return e, operator, task, receipt_for_body(build_request(config, ctx).encode())

    def test_missing_ledger_cutover_and_task_permission_each_block_without_attempt(self):
        e, _, task, _ = self.admitted_terra(False)
        with self.assertRaisesRegex(psycopg.Error,'SERIES_LEDGER_REQUIRED'):
            self.er['worker'].claim(e,'LIVE',task_id=task)
        self.open_test_ledger()
        self.open_test_ledger()
        with self.assertRaisesRegex(psycopg.Error,'SERIES_OPENING_CONFLICT'):
            self.open_test_ledger(opening=0)
        with self.assertRaisesRegex(psycopg.Error,'SERIES_UPSTREAM_CUTOVER_REQUIRED'):
            self.er['worker'].claim(e,'LIVE',task_id=task)
        self.record_test_cutover()
        with self.assertRaisesRegex(psycopg.Error,'SERIES_TASK_NOT_AUTHORIZED'):
            self.er['worker'].claim(e,'LIVE',task_id=task)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_attempts'),0)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('operator','INSERT INTO fi.series_permits(task_id) VALUES(%s) RETURNING task_id',(task,))

    def test_site_verifier_profile_cannot_be_changed_by_experiment(self):
        self.open_test_ledger()
        self.record_test_cutover()
        e,_,_=self.make(mode='FORMAL')
        operator=DeliveryRepository(self.dsns['operator'])
        other={**self.profile_json,'max_crl_age_seconds':1800}
        with self.assertRaisesRegex(psycopg.Error,'SERIES_TRUST_PROFILE_MISMATCH'):
            operator.capture(e,'REGISTRATION',base.previous.PLAN,other)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.commitment_batches'),0)
        self.assertTrue(operator.capture(e,'REGISTRATION',base.previous.PLAN,self.profile_json))

    def test_concurrent_experiments_cannot_spend_the_same_balance(self):
        items=[self.admitted_terra(False) for _ in range(3)]
        self.open_test_ledger(calls=3)
        self.record_test_cutover()
        for _,_,task,_ in items:
            self.admin('INSERT INTO fi.series_permits(task_id) VALUES(%s) RETURNING task_id',(task,))
        def claim(item):
            e,_,task,_=item
            try: return self.er['worker'].claim(e,'LIVE',task_id=task) is not None
            except psycopg.errors.ObjectNotInPrerequisiteState as exc:
                if 'SERIES_BUDGET_EXHAUSTED' not in str(exc): raise
                return False
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(sorted(pool.map(claim,items)),[False,True,True])
        self.assertEqual(self.admin('SELECT balance_microusd FROM fi.series_budget_status'),20000)

    def test_fixed_terra_synthetic_formal_retains_both_time_gates(self):
        e, operator, task, count = self.admitted_terra()
        class Transport:
            def send(self,provider,model,body,*args,**kwargs):
                assert provider=='openai' and model=='gpt-6.1-terra'
                assert strict_json(body)['reasoning']=={'effort':'medium','mode':'standard'}
                data = strict_json(fixture.wire([200000]*5).body)
                data.update(model=model, reasoning={'effort':'medium','mode':'standard'}, service_tier='default')
                return WireResponse(200, encode(data), 'req_synthetic_formal')
        for target,record,keys in [(None,None,{}),(task,None,{}),(task,count,{}),
                                   (task,{}, {'OPENAI_API_KEY':'synthetic'}),
                                   (task,{**count,'source_request_sha256':'a'*64},{'OPENAI_API_KEY':'synthetic'})]:
            result=run_once(self.er['worker'],e,Transport(),keys,task_id=target,input_count_record=record)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(self.admin('SELECT count(*) FROM fi.task_attempts WHERE task_id=%s',(task,)),0)
            self.assertEqual(self.r['operator'].summary(e)['reserved_microusd'],0)
        result=run_once(self.er['worker'],e,Transport(),{'OPENAI_API_KEY':'synthetic'},task_id=task,input_count_record=count)
        self.assertEqual(result['status'],'OK');self.assertIsNone(result['actual_cost'])
        self.assertEqual(result['generation_contract_status'],'MATCH')
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',(result['attempt_id'],)),'HELD_UNKNOWN')
        forecast=operator.capture(e,'FORECAST',base.previous.PLAN,self.profile_json);operator.seal(forecast)
        self.anchor(forecast)
        with self.assertRaises(psycopg.Error):self.er['worker'].claim(e,'LIVE')

    def test_formal_unconfirmed_response_stops_after_persisting_the_observation(self):
        e, _, task, count = self.admitted_terra()
        class Transport:
            def send(self, *args, **kwargs):
                return fixture.wire([200000]*5)
        result = run_once(self.er['worker'], e, Transport(), {'OPENAI_API_KEY':'synthetic'},
                          task_id=task, input_count_record=count)
        self.assertEqual((result['status'], result['provider_status'], result['generation_contract_status']),
                         ('BLOCKED', 'OK', 'MISMATCH'))
        self.assertEqual(self.admin('SELECT probabilities_ppm FROM fi.task_submissions WHERE task_id=%s',(task,)),[200000]*5)
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (result['attempt_id'],)), 'HELD_UNKNOWN')

    def test_helper_not_public_and_diagnostic_not_promoted(self):
        from psycopg.types.json import Jsonb
        e,_,_=self.make()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker','SELECT fi.valid_terra_generation(%s)',(Jsonb({}),))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (e,'FORMAL',5,3600,'Cannot promote existing diagnostic'))
