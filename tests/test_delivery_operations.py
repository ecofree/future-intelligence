from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime,timedelta,timezone
import io
import json
import runpy
from unittest.mock import Mock, patch
import unittest
import test_live_providers as fixture
from future_intelligence.delivery.operations import bls_value,fetch_bls,PublicHTTPS,supervise,acceptance_gates
from future_intelligence.delivery.ots import verify as verify_ots


def legacy_worker(repo,wire):
    """Exercise the CLI and real supervisor/worker with synthetic DB and HTTP."""
    output=io.StringIO();sleep=Mock();code=0
    def bounded(step,**kwargs):return supervise(step,sleep=sleep,**kwargs)
    env={'FI_WORKER_DATABASE_URL':'fixture-dsn','OPENAI_API_KEY':'synthetic-invalid-api-key'}
    argv=['future_intelligence.delivery','worker','fixture-experiment','--cycles','2','--allow-network']
    with patch.dict('os.environ',env,clear=True),patch('sys.argv',argv),redirect_stdout(output), \
         patch('future_intelligence.delivery.repository.DeliveryRepository') as gate, \
         patch('future_intelligence.evidence.repository.EvidenceRepository',return_value=repo), \
         patch('future_intelligence.tasks.live_worker.HTTPSOnce',return_value=wire), \
         patch('future_intelligence.delivery.operations.supervise',side_effect=bounded):
        gate.return_value.one.return_value={'ready':True}
        try:runpy.run_module('future_intelligence.delivery',run_name='__main__')
        except SystemExit as exited:code=exited.code
    return code,json.loads(output.getvalue()),sleep


class OperationsTests(unittest.TestCase):
    def document(self):return {'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'CUUR0000SA0','data':[{'year':'2026','period':'M01','value':'320.1'}]}]}}
    def test_exact_period_not_latest(self):
        self.assertIsNone(bls_value(self.document(),'CUUR0000SA0',2026,'M02'))
        self.assertEqual(bls_value(self.document(),'CUUR0000SA0',2026,'M01'),'320.1')
    def test_ambiguous_and_nonfinite(self):
        d=self.document();d['Results']['series'][0]['data']*=2
        with self.assertRaises(ValueError):bls_value(d,'CUUR0000SA0',2026,'M01')
        d=self.document();d['Results']['series'][0]['data'][0]['value']='NaN'
        with self.assertRaises(ValueError):bls_value(d,'CUUR0000SA0',2026,'M01')
    def test_source_permission_and_path(self):
        with self.assertRaises(PermissionError):fetch_bls('CUUR0000SA0',2026,'M01')
        with self.assertRaises(ValueError):fetch_bls('../admin',2026,'M01',permit=True)
    def test_private_dns_denied(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):PublicHTTPS('api.bls.gov').connect()
    def test_worker_stops_on_uncertain(self):
        calls=[]
        def fail():calls.append(1);raise TimeoutError('internal')
        r=supervise(fail,cycles=10,interval=1,sleep=lambda _:None)
        self.assertEqual(len(calls),1);self.assertEqual(r[0]['status'],'STOPPED_UNCERTAIN')
    def test_worker_stops_on_missing_key(self):
        self.assertEqual(len(supervise(lambda:{'status':'BLOCKED'},cycles=10,interval=1,sleep=lambda _:None)),1)
    def test_bounded_runner(self):
        self.assertEqual(len(supervise(lambda:{'status':'OK'},cycles=3,interval=1,sleep=lambda _:None)),3)
        with self.assertRaises(ValueError):supervise(lambda:{},cycles=0,interval=1)
    def test_legacy_worker_401_stops_after_recording_first_attempt_and_exits_nonzero(self):
        class TwoTasks(fixture.FakeRepo):
            def __init__(self):
                super().__init__();self.pending=['first-task','second-task']
            def claim(self,e,k):
                lease=deepcopy(super().claim(e,k))
                lease['task_id']=self.pending.pop(0)
                return lease
        repo=TwoTasks()
        wire=fixture.FakeWire(fixture.WireResponse(401,b'synthetic-error-body-must-stay-private'))
        code,results,sleep=legacy_worker(repo,wire)
        self.assertEqual(code,2)
        self.assertEqual(repo.events,['claim','context','prepare','observe','fail'])
        self.assertEqual(repo.pending,['second-task'])
        self.assertEqual(wire.calls,1);sleep.assert_not_called()
        self.assertEqual(repo.obs['http_status'],401)
        self.assertEqual(repo.obs['status'],'HTTP_ERROR')
        self.assertEqual(repo.failure,('HTTP_ERROR',None,False))
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['task_id'],'first-task')
        self.assertEqual(results[0]['http_status'],401)
        self.assertIsNone(results[0]['actual_cost'])
        self.assertIsNone(results[0]['estimated_microusd'])
        public=json.dumps({'observation':repo.obs,'results':results})
        self.assertNotIn('synthetic-invalid-api-key',public)
        self.assertNotIn('synthetic-error-body-must-stay-private',public)
    def test_legacy_worker_persistence_failure_exits_uncertain_without_claiming_again(self):
        for method in ('observe','fail'):
            with self.subTest(method=method):
                repo=fixture.FakeRepo()
                wire=fixture.FakeWire(fixture.WireResponse(401,b'synthetic-error-body-must-stay-private'))
                with patch.object(repo,method,side_effect=ConnectionError('synthetic-private-db-error')):
                    code,results,sleep=legacy_worker(repo,wire)
                self.assertEqual(code,2)
                expected=['claim','context','prepare']+(['observe'] if method=='fail' else [])
                self.assertEqual(repo.events,expected)
                self.assertEqual(wire.calls,1);sleep.assert_not_called()
                self.assertEqual(hasattr(repo,'obs'),method=='fail')
                self.assertFalse(hasattr(repo,'failure'))
                self.assertEqual(results,[{'status':'STOPPED_UNCERTAIN','reason':'NO_AUTOMATIC_REDISPATCH'}])
                self.assertNotIn('synthetic-private-db-error',json.dumps(results))
    def test_legacy_worker_existing_stop_results_exit_nonzero(self):
        for status in ('BLOCKED','NO_REDISPATCH'):
            with self.subTest(status=status):
                repo=fixture.FakeRepo();wire=fixture.FakeWire()
                if status=='BLOCKED':repo.lease['entry']['prompt_sha256']='0'*64
                else:repo.marker=False
                code,results,sleep=legacy_worker(repo,wire)
                self.assertEqual(code,2)
                self.assertEqual(len(results),1)
                self.assertEqual(results[0]['status'],status)
                self.assertEqual(repo.events.count('claim'),1)
                self.assertEqual(wire.calls,0);sleep.assert_not_called()
    def test_legacy_nonworker_output_unchanged(self):
        output=io.StringIO()
        with patch.dict('os.environ',{'FI_OPERATOR_DATABASE_URL':'fixture-dsn'},clear=True), \
             patch('sys.argv',['future_intelligence.delivery','recover','fixture-experiment']), \
             patch('future_intelligence.delivery.repository.DeliveryRepository'), \
             patch('future_intelligence.tasks.repository.TaskRepository') as tasks,redirect_stdout(output):
            tasks.return_value.recover.return_value=0
            runpy.run_module('future_intelligence.delivery',run_name='__main__')
        self.assertEqual(json.loads(output.getvalue()),{'recovered':0})
    def test_live_acceptance_cannot_be_fabricated_by_counts(self):
        x=acceptance_gates(live_providers=[],real_tsa=False,confirmed_outcomes=0,blind_review=False)
        self.assertEqual(x['production_acceptance'],'BLOCKED');self.assertEqual(len(x['outstanding_gates']),6)
        x=acceptance_gates(live_providers=['openai','anthropic','google'],real_tsa=True,confirmed_outcomes=1,blind_review=True)
        self.assertEqual(x['production_acceptance'],'REQUIRES_EVIDENCE_REVIEW')
    def test_missing_ots_verifier_is_not_success(self):
        with patch('subprocess.run',side_effect=FileNotFoundError()):
            self.assertEqual(verify_ots('a'*64,b'fixture')['status'],'BLOCKED_VERIFIER_OR_NODE')
