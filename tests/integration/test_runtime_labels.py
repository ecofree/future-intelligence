"""Explicit UI labels; model-shaped tests are still synthetic API diagnostics."""
import unittest
import test_live_evidence as previous
from future_intelligence.delivery.repository import DeliveryRepository

class RuntimeLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):previous.LiveEvidenceTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):previous.LiveEvidenceTests.tearDownClass.__func__(cls)
    call=previous.LiveEvidenceTests.call
    admin=previous.LiveEvidenceTests.admin
    question=previous.LiveEvidenceTests.question
    make=previous.LiveEvidenceTests.make

    def test_live_diagnostic_labels_are_not_hidden_in_nested_spec(self):
        e,*_=self.make()
        rows=DeliveryRepository(self.dsns['operator']).overview(experiment_id=e)
        row=next(x for x in rows if str(x['id'])==str(e))
        self.assertEqual(row['run_kind'],'LIVE')
        self.assertEqual(row['operation_mode'],'DIAGNOSTIC')
        self.assertEqual(row['suite'],'A')

    def test_unconfigured_live_never_looks_formal(self):
        e,*_=self.make(operation_mode=None)
        row=next(x for x in DeliveryRepository(self.dsns['operator']).overview(experiment_id=e) if str(x['id'])==str(e))
        self.assertEqual(row['operation_mode'],'UNCONFIGURED')
