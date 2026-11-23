"""Synthetic transport and wall-clock fixtures; no API requests."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch
import test_live_providers as fixture
from future_intelligence.runtime.service import loop
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

    def test_authentication_failure_stops_before_claiming_another_task(self):
        repo=fixture.FakeRepo()
        wire=fixture.FakeWire(fixture.WireResponse(
            401,b'Unauthorized: synthetic-secret-echo-must-not-persist'))
        emitted=[]; sleep=Mock()
        code=loop(lambda:run_once(repo,'exp',wire,{'OPENAI_API_KEY':'synthetic-invalid-key'}),
                  cycles=2,interval=1,emit=emitted.append,sleep=sleep)
        self.assertEqual(code,2)
        self.assertEqual(repo.events,['claim','context','prepare','observe','fail'])
        self.assertEqual(wire.calls,1)
        sleep.assert_not_called()
        self.assertEqual(repo.obs['status'],'HTTP_ERROR')
        self.assertEqual(repo.obs['http_status'],401)
        self.assertIsNone(repo.obs['output'])
        self.assertEqual(repo.failure,('HTTP_ERROR',None,False))
        self.assertEqual(len(emitted),1)
        self.assertEqual(emitted[0]['status'],'HTTP_ERROR')
        self.assertEqual(emitted[0]['http_status'],401)
        self.assertIsNone(emitted[0]['actual_cost'])
        self.assertIsNone(emitted[0]['estimated_microusd'])
        saved=json.dumps({'observation':repo.obs,'emitted':emitted})
        self.assertNotIn('synthetic-invalid-key',saved)
        self.assertNotIn('synthetic-secret-echo-must-not-persist',saved)
