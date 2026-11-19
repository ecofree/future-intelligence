"""Receipt-to-dispatch regression; every repository and response is synthetic."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_forecast_contract as contract
import test_live_providers as fixture
import test_terra_generation as terra
from test_exact_task_worker import ExactRepo, EXPERIMENT, BINARY_TASK
from future_intelligence.delivery import __main__ as delivery_cli
from future_intelligence.providers.input_count import build_count_request
from future_intelligence.providers.live import build_request, encode
from future_intelligence.runtime import __main__ as runtime_cli
from future_intelligence.tasks import live_worker


class CountRepo(ExactRepo):
    def __init__(self, edges=None):
        super().__init__()
        self.lease['entry'] = terra.entry()
        self.packet = contract.context(edges)

    def context(self, lease):
        self.events.append('context')
        return self.packet


def receipt(repo, *, status=200, value=None):
    body = build_request(repo.lease['entry'], repo.packet).encode('utf-8')
    raw = encode({'object': 'response.input_tokens', 'input_tokens': 2345} if value is None else value)
    now = datetime.now(timezone.utc)
    return {
        'protocol': 'fi.openai-input-count.v1',
        'endpoint': 'https://api.openai.com/v1/responses/input_tokens',
        'source_request_sha256': hashlib.sha256(body).hexdigest(),
        'count_request_sha256': hashlib.sha256(build_count_request(body)).hexdigest(),
        'response_body': raw.decode('utf-8'), 'response_sha256': hashlib.sha256(raw).hexdigest(),
        'http_status': status, 'request_id': 'req_synthetic_input_count',
        'started_at': (now - timedelta(seconds=2)).isoformat(),
        'finished_at': (now - timedelta(seconds=1)).isoformat(),
    }


class InputCountWorkerTests(unittest.TestCase):
    def test_matching_count_allows_one_dispatch_for_each_shape(self):
        for edges, probabilities in ((None, [400000, 600000]),
                (['3.0', '3.5', '4.0', '4.5'], [200000] * 5)):
            with self.subTest(edges=edges):
                repo = CountRepo(edges)
                record = receipt(repo)
                wire = fixture.FakeWire(contract.wire(probabilities))
                result = live_worker.run_once(repo, EXPERIMENT, wire, {'OPENAI_API_KEY': 'synthetic'},
                    task_id=BINARY_TASK, input_count_record=record)
                self.assertEqual(result['status'], 'OK')
                self.assertEqual(result['input_count']['input_tokens'], 2345)
                self.assertEqual(result['input_count']['record_sha256'], hashlib.sha256(encode(record)).hexdigest())
                self.assertFalse(result['input_count']['independent_time_proof'])
                self.assertEqual(repo.events, ['claim', 'context', 'prepare', 'observe', 'succeed'])
                self.assertEqual(wire.calls, 1)
                self.assertIsNone(repo.actual)  # Successful completion still holds unknown cost.
                self.assertNotIn('response_body', json.dumps(result))

    def test_failed_count_or_generation_usage_cannot_prepare_or_dispatch(self):
        for status, value in ((400, {'error': {'message': 'synthetic-private-error'}}),
                (200, {'object': 'response', 'usage': {'input_tokens': 2345}}),
                (200, {'object': 'response.input_tokens', 'input_tokens': 30001})):
            with self.subTest(status=status, value=value):
                repo = CountRepo(); wire = fixture.FakeWire()
                result = live_worker.run_once(repo, EXPERIMENT, wire, {'OPENAI_API_KEY': 'synthetic'},
                    task_id=BINARY_TASK, input_count_record=receipt(repo, status=status, value=value))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['reason'], 'INPUT_COUNT_VALIDATION_FAILED')
                self.assertEqual(repo.events, ['claim', 'context', 'fail'])
                self.assertEqual(repo.failure, ('INPUT_COUNT_VALIDATION_FAILED', 0, False))
                self.assertEqual(wire.calls, 0)
                self.assertNotIn('synthetic-private-error', json.dumps(result))

    def test_changed_exact_request_cannot_reuse_receipt(self):
        for change in ('input', 'max_output_tokens'):
            with self.subTest(change=change):
                repo = CountRepo(); record = receipt(repo); wire = fixture.FakeWire()
                if change == 'input':repo.packet['evidence']['items'][0]['text'] += ' changed'
                else:repo.lease['entry']['parameters']['max_output_tokens'] += 1
                result = live_worker.run_once(repo, EXPERIMENT, wire, {'OPENAI_API_KEY': 'synthetic'},
                    task_id=BINARY_TASK, input_count_record=record)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(repo.events, ['claim', 'context', 'fail'])
                self.assertEqual(wire.calls, 0)

    def test_record_without_exact_task_is_rejected_before_claim(self):
        repo = CountRepo(); wire = fixture.FakeWire()
        result = live_worker.run_once(repo, EXPERIMENT, wire, {}, input_count_record={})
        self.assertEqual(result, {'status': 'BLOCKED', 'reason': 'INPUT_COUNT_REQUIRES_EXACT_TASK'})
        self.assertEqual(repo.events, [])
        self.assertEqual(wire.calls, 0)

    def test_cli_record_requires_task_before_database_or_secret_read(self):
        args = ['worker', EXPERIMENT, '--input-count-record', 'unused.json', '--allow-network']
        with patch.object(runtime_cli, 'read_secret') as secret:
            with self.assertRaisesRegex(ValueError, 'requires --task'):runtime_cli.main(args)
            secret.assert_not_called()
        with patch('sys.argv', ['delivery'] + args), patch.object(delivery_cli, 'DeliveryRepository') as db:
            with self.assertRaisesRegex(ValueError, 'requires --task'):delivery_cli.main()
            db.assert_not_called()
        with patch('sys.argv', ['live_worker', EXPERIMENT, '--input-count-record', 'unused.json']), \
                patch('future_intelligence.evidence.repository.EvidenceRepository') as db, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_code:live_worker.main()
            self.assertEqual(exit_code.exception.code, 2)
            db.assert_not_called()

    def test_file_parser_rejects_oversized_duplicate_and_invalid_receipts_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'count.json'
            for raw in (b'{' + b' ' * 2_000_001, b'{"x":1,"x":2}', b'[]', b'private-invalid-json'):
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
                    live_worker.load_count_record(path)

    def test_all_three_clis_enforce_the_supplied_failed_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'count.json'
            for kind in ('runtime', 'delivery', 'single'):
                with self.subTest(kind=kind):
                    repo = CountRepo(); wire = fixture.FakeWire()
                    path.write_text(json.dumps(receipt(repo, status=400)), encoding='utf-8')
                    output = io.StringIO()
                    env = {'FI_WORKER_DATABASE_URL': 'synthetic-unused-dsn', 'OPENAI_API_KEY': 'synthetic'}
                    with patch.dict('os.environ', env, clear=True), redirect_stdout(output), \
                            patch('future_intelligence.evidence.repository.EvidenceRepository', return_value=repo), \
                            patch.object(live_worker, 'HTTPSOnce', return_value=wire), patch('signal.signal'), \
                            patch('future_intelligence.delivery.repository.DeliveryRepository') as gate, \
                            patch.object(delivery_cli, 'DeliveryRepository') as old_gate:
                        gate.return_value.one.return_value = {'ready': True}
                        old_gate.return_value.one.return_value = {'ready': True}
                        args = ['worker', EXPERIMENT, '--task', BINARY_TASK,
                                '--input-count-record', str(path), '--allow-network']
                        if kind == 'runtime':
                            self.assertEqual(runtime_cli.main(args), 2)
                            result = json.loads(output.getvalue())
                        elif kind == 'delivery':
                            with patch('sys.argv', ['delivery'] + args):result = delivery_cli.main()[0]
                        else:
                            with patch('sys.argv', ['live_worker', EXPERIMENT, '--task', BINARY_TASK,
                                    '--input-count-record', str(path)]):
                                self.assertEqual(live_worker.main(), 2)
                            result = json.loads(output.getvalue())
                    self.assertEqual(result['status'], 'BLOCKED')
                    self.assertEqual(repo.selected, [BINARY_TASK])
                    self.assertNotIn('prepare', repo.events)
                    self.assertEqual(wire.calls, 0)

    def test_exact_task_unavailable_is_nonzero_without_fallback(self):
        repo = CountRepo(); repo.claim = Mock(return_value=None)
        output = io.StringIO()
        with patch.dict('os.environ', {'FI_WORKER_DATABASE_URL': 'synthetic'}, clear=True), \
                patch('sys.argv', ['live_worker', EXPERIMENT, '--task', BINARY_TASK]), \
                patch('future_intelligence.evidence.repository.EvidenceRepository', return_value=repo), \
                redirect_stdout(output):
            self.assertEqual(live_worker.main(), 2)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'TARGET_TASK_NOT_CLAIMABLE')
        repo.claim.assert_called_once_with(EXPERIMENT, 'LIVE', task_id=BINARY_TASK)
