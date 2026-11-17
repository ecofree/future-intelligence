"""No model/TSA requests: pure runtime contracts and secret handling."""
from datetime import datetime,timezone
from pathlib import Path
import os
import tempfile
import unittest
from future_intelligence.runtime.release import event_cluster,strict_document
from future_intelligence.runtime.secrets import read_secret,inject
from future_intelligence.runtime.service import alerts,operator_tick,loop

class RuntimeTests(unittest.TestCase):
    def test_cluster_across_series(self):
        self.assertEqual(event_cluster('CPI','2027-01-14T08:30:00-05:00'),event_cluster('CPI','2027-01-14T13:30:00Z'))
    def test_distinct_release_families(self):
        self.assertNotEqual(event_cluster('CPI','2027-01-14T13:30:00Z'),event_cluster('PPI','2027-01-14T13:30:00Z'))
    def test_no_unknown_family(self):
        with self.assertRaises(ValueError):event_cluster('unreviewed','2027-01-14T13:30:00Z')
    def test_no_naive_cluster_time(self):
        with self.assertRaises(ValueError):event_cluster('CPI','2027-01-14T13:30:00')
    def test_duplicate_document_keys(self):
        with self.assertRaises(ValueError):strict_document(b'{"x":0,"x":1}')
    def test_nonfinite_document(self):
        with self.assertRaises(ValueError):strict_document(b'{"x":NaN}')
    def test_bounded_document(self):
        with self.assertRaises(ValueError):strict_document(b' '*2_000_001)
    def test_secret_presence_not_validation(self):
        self.assertEqual(read_secret('A',{'A':'test-only'}),'test-only')
    def test_secret_no_ambiguous_source(self):
        with self.assertRaises(ValueError):read_secret('A',{'A':'x','A_FILE':'anything'})
    def test_missing_secret(self):
        with self.assertRaises(ValueError):read_secret('A',{})
        self.assertIsNone(read_secret('A',{},required=False))
    def test_private_file(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'secret';p.write_text('test-value');p.chmod(0o600)
            self.assertEqual(read_secret('A',{'A_FILE':str(p)}),'test-value')
            p.chmod(0o644)
            with self.assertRaises(ValueError):read_secret('A',{'A_FILE':str(p)})
    def test_no_symlink_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'secret';p.write_text('test-value');p.chmod(0o600)
            link=Path(folder)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):read_secret('A',{'A_FILE':str(link)})
    def test_inject_only_allowed_names(self):
        self.assertEqual(inject(['A'],{'A':'x','B':'y'}),{'A':'x'})
    def test_unknown_cost_alert(self):
        self.assertIn('COST_RECONCILIATION_REQUIRED',alerts({'held_unknown':1}))
    def test_budget_includes_reservations(self):
        self.assertIn('BUDGET_NEAR_LIMIT',alerts({'budget_microusd':100,'spent_microusd':0,'reserved_microusd':80}))
    def test_budget_overrun_not_clipped(self):
        self.assertIn('BUDGET_OVERRUN',alerts({'budget_microusd':100,'spent_microusd':101}))
    def test_bounded_stop(self):
        out=[];calls=[]
        code=loop(lambda:calls.append(1),cycles=3,interval=1,emit=out.append,stop=lambda:True,sleep=lambda _:None)
        self.assertEqual(code,0);self.assertEqual(calls,[]);self.assertEqual(out,[{'status':'STOP_REQUESTED'}])
    def test_ambiguous_failure_never_redispatched(self):
        calls=[];out=[]
        def step():calls.append(1);raise RuntimeError('secret must not be in log')
        self.assertEqual(loop(step,cycles=5,interval=1,emit=out.append,sleep=lambda _:None),2)
        self.assertEqual(len(calls),1);self.assertNotIn('secret',str(out))
    def test_invalid_cycles(self):
        with self.assertRaises(ValueError):loop(lambda:None,cycles=True,interval=1,emit=lambda _:None)
    def test_seal_does_not_claim_external_time(self):
        class Tasks:
            def recover(self,e):return 0
        class Repo:
            def one(self,*_):return dict(mode='FORMAL',dispatch_stop_at='2026-01-01T00:00:00Z',registration_batch='r')
            def capture(self,e,kind):return 'final'
            def seal(self,b):return {'subject_sha256':'a'*64}
        result=operator_tick(Repo(),Tasks(),'test',now=datetime(2027,1,1,tzinfo=timezone.utc))
        self.assertEqual(result['status'],'FORECAST_SEALED')
        self.assertEqual(result['external_timestamp'],'REQUIRES_SEPARATE_ANCHOR_AND_VERIFIER')
    def test_diagnostic_not_auto_sealed(self):
        class Tasks:
            def recover(self,e):return 0
        class Repo:
            def one(self,*_):return dict(mode='DIAGNOSTIC',dispatch_stop_at='2026-01-01T00:00:00Z')
            def capture(self,*_):raise AssertionError('must not capture diagnostics')
        self.assertEqual(operator_tick(Repo(),Tasks(),'test')['status'],'OBSERVED')
