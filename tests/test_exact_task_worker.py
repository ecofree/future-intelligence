"""Exact task dispatch with synthetic repositories/transports; no real calls."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import test_live_providers as fixture
from future_intelligence.delivery import __main__ as delivery_cli
from future_intelligence.runtime import __main__ as runtime_cli
from future_intelligence.tasks import live_worker
from future_intelligence.tasks.repository import TaskRepository


EXPERIMENT = '10000000-0000-0000-0000-000000000001'
BINARY_TASK = 'f0000000-0000-0000-0000-000000000001'
BINNED_TASK = '20000000-0000-0000-0000-000000000001'


class ExactRepo(fixture.FakeRepo):
    def __init__(self):
        super().__init__()
        self.lease['task_id'] = BINARY_TASK
        self.selected = []

    def claim(self, experiment, kind, *, task_id=None):
        self.selected.append(task_id)
        return super().claim(experiment, kind)


class ExactTaskWorkerTests(unittest.TestCase):
    def test_repository_routes_exact_and_legacy_calls_separately(self):
        repo = TaskRepository('synthetic-unused-dsn')
        repo._call = Mock(return_value=None)
        repo.claim(EXPERIMENT, 'LIVE', task_id=UUID(BINARY_TASK))
        repo._call.assert_called_once_with('SELECT fi.claim_task(%s::uuid,%s,%s::uuid)',
                                          (EXPERIMENT, 'LIVE', BINARY_TASK))
        repo._call.reset_mock()
        repo.claim(EXPERIMENT, 'LIVE')
        repo._call.assert_called_once_with('SELECT fi.claim_task(%s::uuid,%s)', (EXPERIMENT, 'LIVE'))
        repo._call.reset_mock()
        with self.assertRaises(ValueError):
            repo.claim(EXPERIMENT, 'LIVE', task_id='invalid-task')
        repo._call.assert_not_called()

    def test_exact_task_reaches_claim_and_keeps_actual_identity(self):
        repo = ExactRepo(); wire = fixture.FakeWire()
        result = live_worker.run_once(repo, EXPERIMENT, wire, {'OPENAI_API_KEY': 'synthetic'},
                                      task_id=BINARY_TASK)
        self.assertEqual(repo.selected, [BINARY_TASK])
        self.assertEqual(result['task_id'], BINARY_TASK)
        self.assertEqual(result['attempt_id'], repo.lease['attempt_id'])
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(wire.calls, 1)

    def test_wrong_lease_is_blocked_before_context_prepare_or_send(self):
        repo = ExactRepo(); repo.lease['task_id'] = BINNED_TASK
        wire = fixture.FakeWire()
        result = live_worker.run_once(repo, EXPERIMENT, wire, {'OPENAI_API_KEY': 'synthetic'},
                                      task_id=BINARY_TASK)
        self.assertEqual(repo.events, ['claim', 'fail'])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason'], 'CLAIMED_TASK_MISMATCH')
        self.assertEqual(result['task_id'], BINNED_TASK)  # Never relabel the actual lease.
        self.assertEqual(repo.failure, ('CLAIMED_TASK_MISMATCH', 0, False))
        self.assertEqual(wire.calls, 0)

    def test_unavailable_exact_task_does_not_retry_without_selector(self):
        repo = ExactRepo(); repo.claim = Mock(return_value=None)
        wire = fixture.FakeWire()
        result = live_worker.run_once(repo, EXPERIMENT, wire, {}, task_id=BINARY_TASK)
        repo.claim.assert_called_once_with(EXPERIMENT, 'LIVE', task_id=BINARY_TASK)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason'], 'TARGET_TASK_NOT_CLAIMABLE')
        self.assertEqual(result['requested_task_id'], BINARY_TASK)
        self.assertNotIn('task_id', result)
        self.assertNotIn('attempt_id', result)
        self.assertEqual(wire.calls, 0)
        self.assertEqual(repo.events, [])

    def test_exact_task_rejects_repeated_cycles_before_credentials_or_database(self):
        args = ['worker', EXPERIMENT, '--task', BINARY_TASK, '--cycles', '2', '--allow-network']
        with patch.object(runtime_cli, 'read_secret') as secret:
            with self.assertRaisesRegex(ValueError, '--cycles 1'):
                runtime_cli.main(args)
            secret.assert_not_called()
        with patch('sys.argv', ['delivery'] + args), patch.object(delivery_cli, 'DeliveryRepository') as repo:
            with self.assertRaisesRegex(ValueError, '--cycles 1'):
                delivery_cli.main()
            repo.assert_not_called()

    def test_all_worker_clis_forward_exact_task_without_relabeling(self):
        for kind in ('runtime', 'delivery', 'single'):
            with self.subTest(kind=kind):
                repo = ExactRepo(); wire = fixture.FakeWire()
                env = {'FI_WORKER_DATABASE_URL': 'synthetic-unused-dsn', 'OPENAI_API_KEY': 'synthetic'}
                output = io.StringIO()
                with patch.dict('os.environ', env, clear=True), redirect_stdout(output), \
                     patch('future_intelligence.evidence.repository.EvidenceRepository', return_value=repo), \
                     patch.object(live_worker, 'HTTPSOnce', return_value=wire), \
                     patch('signal.signal'), \
                     patch('future_intelligence.delivery.repository.DeliveryRepository') as gate, \
                     patch.object(delivery_cli, 'DeliveryRepository') as old_gate:
                    gate.return_value.one.return_value = {'ready': True}
                    old_gate.return_value.one.return_value = {'ready': True}
                    args = ['worker', EXPERIMENT, '--task', BINARY_TASK, '--allow-network']
                    if kind == 'runtime':
                        self.assertEqual(runtime_cli.main(args), 0)
                        result = json.loads(output.getvalue())
                    elif kind == 'delivery':
                        with patch('sys.argv', ['delivery'] + args):
                            result = delivery_cli.main()[0]
                    else:
                        with patch('sys.argv', ['live_worker', EXPERIMENT, '--task', BINARY_TASK]):
                            self.assertEqual(live_worker.main(), 0)
                        result = json.loads(output.getvalue())
                self.assertEqual(repo.selected, [BINARY_TASK])
                self.assertEqual(result['task_id'], BINARY_TASK)
                self.assertEqual(result['status'], 'OK')
                self.assertEqual(wire.calls, 1)
