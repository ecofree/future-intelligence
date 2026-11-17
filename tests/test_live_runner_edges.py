"""Synthetic transport and wall-clock fixtures; no API requests."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import test_live_providers as fixture
from future_intelligence.tasks.live_worker import run_once


class LiveRunnerEdgeTests(unittest.TestCase):
    def test_prepare_wait_consumes_lease_no_send(self):
        repo=fixture.FakeRepo(); wire=fixture.FakeWire()
        end=datetime.fromisoformat(repo.lease['lease_until'])
        with patch('future_intelligence.tasks.live_worker.datetime') as clock:
            clock.fromisoformat.side_effect=datetime.fromisoformat
            clock.now.side_effect=[end-timedelta(seconds=100),end-timedelta(seconds=2)]
            result=run_once(repo,'exp',wire,{'OPENAI_API_KEY':'fixture-key'})
        self.assertEqual(wire.calls,0)
        self.assertEqual(result['status'],'TIMEOUT')
        self.assertEqual(repo.events[-2:],['observe','fail'])
        self.assertIsNone(repo.failure[1])

    def test_empty_output_not_silently_fixed(self):
        parsed=fixture.parse_response('openai',fixture.response(value={}),2,{'e1'})
        self.assertEqual(parsed['status'],'INVALID_OUTPUT')
        self.assertIsNone(parsed['output'])
