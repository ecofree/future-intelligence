from copy import deepcopy
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import unittest
from future_intelligence.delivery.operations import bls_value,fetch_bls,PublicHTTPS,supervise,acceptance_gates
from future_intelligence.delivery.ots import verify as verify_ots


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
    def test_live_acceptance_cannot_be_fabricated_by_counts(self):
        x=acceptance_gates(live_providers=[],real_tsa=False,confirmed_outcomes=0,blind_review=False)
        self.assertEqual(x['production_acceptance'],'BLOCKED');self.assertEqual(len(x['outstanding_gates']),6)
        x=acceptance_gates(live_providers=['openai','anthropic','google'],real_tsa=True,confirmed_outcomes=1,blind_review=True)
        self.assertEqual(x['production_acceptance'],'REQUIRES_EVIDENCE_REVIEW')
    def test_missing_ots_verifier_is_not_success(self):
        with patch('subprocess.run',side_effect=FileNotFoundError()):
            self.assertEqual(verify_ots('a'*64,b'fixture')['status'],'BLOCKED_VERIFIER_OR_NODE')
