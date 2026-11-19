"""Opt-in response metadata; synthetic responses only, no real requests."""
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_forecast_contract as contract
import test_live_providers as fixture
import test_terra_generation as terra
from future_intelligence.providers.live import Blocked, WireResponse, build_request, encode, parse_response, strict_json
from future_intelligence.tasks.live_worker import run_once


FIELDS = ('reasoning_effort', 'reasoning_mode', 'service_tier')
MISSING = {name: {'state': 'MISSING', 'value': None} for name in FIELDS}
PRESENT = {name: {'state': 'PRESENT', 'value': value} for name, value in
           zip(FIELDS, ('medium', 'standard', 'default'))}


def payload():
    data = strict_json(fixture.response().body)
    data.update(id='resp_metadata_fixture', model='gpt-6.1-terra',
                reasoning={'effort': 'medium', 'mode': 'standard'}, service_tier='default')
    return data


def response(data=None, *, request_id='req_metadata_fixture'):
    return WireResponse(200, encode(payload() if data is None else data), request_id)


def parse(wire):
    return parse_response('openai', wire, 2, {'e1'}, capture_generation=True)


class MetadataRepo(fixture.FakeRepo):
    def __init__(self, edges=None):
        super().__init__()
        self.lease['entry'] = terra.entry()
        self.packet = contract.context(edges)
        self.prepared_body = None

    def context(self, lease):
        self.events.append('context')
        return self.packet

    def prepare(self, lease, body):
        self.prepared_body = body
        return super().prepare(lease, body)


class RecordingWire(fixture.FakeWire):
    def send(self, *args, **kwargs):
        self.sent_body = args[2]
        return super().send(*args, **kwargs)


class ProviderMetadataParserTests(unittest.TestCase):
    def test_v2_separates_http_request_id_and_response_id(self):
        obs = parse(response())
        self.assertEqual(obs['protocol'], 'fi.provider-observation.v2')
        self.assertEqual(obs['request_id'], 'req_metadata_fixture')
        self.assertEqual(obs['response_id'], 'resp_metadata_fixture')
        self.assertEqual(obs['generation_reported'], PRESENT)
        self.assertEqual(obs['status'], 'OK')
        self.assertEqual(set(obs), {'protocol', 'status', 'http_status', 'response_sha256',
            'model_reported', 'request_id', 'usage', 'output', 'response_id', 'generation_reported'})
        self.assertIsNone(parse(response(request_id=None))['request_id'])
        data = payload(); del data['id']
        obs = parse(response(data))
        self.assertEqual(obs['request_id'], 'req_metadata_fixture')
        self.assertIsNone(obs['response_id'])

    def test_default_v1_observation_is_unchanged_field_for_field(self):
        wire = response()
        expected = {'protocol': 'fi.provider-observation.v1', 'status': 'OK',
            'http_status': 200, 'response_sha256': hashlib.sha256(wire.body).hexdigest(),
            'model_reported': 'gpt-6.1-terra', 'request_id': 'resp_metadata_fixture',
            'usage': {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 10},
            'output': fixture.output()}
        self.assertEqual(parse_response('openai', wire, 2, {'e1'}), expected)
        self.assertEqual(parse_response('openai', wire, 2, {'e1'}, capture_generation=False), expected)

    def test_capture_is_explicit_and_only_for_official_openai_adapter(self):
        for provider in ('anthropic', 'google', 'openai_compatible', 'unknown'):
            with self.subTest(provider=provider), self.assertRaisesRegex(Blocked, 'INVALID_GENERATION_CAPTURE'):
                parse_response(provider, response(), 2, {'e1'}, capture_generation=True)
        for value in (None, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(Blocked):
                parse_response('openai', response(), 2, {'e1'}, capture_generation=value)

    def test_each_allowed_label_is_preserved_without_assuming_a_contract_match(self):
        values = {'reasoning_effort': ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
                  'reasoning_mode': ('standard', 'pro'), 'service_tier': ('auto', 'default', 'flex', 'priority', 'scale')}
        for name, allowed in values.items():
            for value in allowed:
                data = payload()
                if name == 'service_tier':data[name] = value
                else:data['reasoning'][name.removeprefix('reasoning_')] = value
                with self.subTest(name=name, value=value):
                    obs = parse(response(data))
                    self.assertEqual(obs['generation_reported'][name], {'state': 'PRESENT', 'value': value})
                    self.assertEqual(obs['status'], 'OK')

    def test_missing_and_null_labels_are_explicitly_missing(self):
        for null in (False, True):
            data = payload()
            if null:data.update(reasoning=None, service_tier=None)
            else:del data['reasoning']; del data['service_tier']
            self.assertEqual(parse(response(data))['generation_reported'], MISSING)
        for name in FIELDS:
            for null in (False, True):
                data = payload()
                target = data if name == 'service_tier' else data['reasoning']
                key = name if name == 'service_tier' else name.removeprefix('reasoning_')
                if null:target[key] = None
                else:del target[key]
                with self.subTest(name=name, null=null):
                    self.assertEqual(parse(response(data))['generation_reported'][name],
                                     {'state': 'MISSING', 'value': None})

    def test_invalid_labels_and_private_reasoning_are_never_echoed(self):
        for name in FIELDS:
            for value in ('private-secret-sentinel', True, 2, [], {'private': 'private-secret-sentinel'}):
                data = payload()
                if name == 'service_tier':data[name] = value
                else:data['reasoning'][name.removeprefix('reasoning_')] = value
                data['reasoning']['summary'] = 'private-summary-sentinel'
                data['output'].insert(0, {'type': 'reasoning', 'summary': 'private-block-sentinel'})
                data['unrecognized'] = 'private-extra-sentinel'
                with self.subTest(name=name, value=value):
                    obs = parse(response(data))
                    self.assertEqual(obs['status'], 'OK')
                    self.assertEqual(obs['generation_reported'][name], {'state': 'INVALID', 'value': None})
                    self.assertNotIn('private-', json.dumps(obs))
        for value in ('private-reasoning-sentinel', 0, False, [], ['private-reasoning-sentinel']):
            data = payload(); data['reasoning'] = value
            obs = parse(response(data))
            for name in ('reasoning_effort', 'reasoning_mode'):
                self.assertEqual(obs['generation_reported'][name], {'state': 'INVALID', 'value': None})
            self.assertNotIn('private-reasoning-sentinel', json.dumps(obs))

    def test_wire_and_http_failures_keep_v2_shape_without_parsing_error_bodies(self):
        for wire, status in ((WireResponse(None, b'', 'req_failed', 'TIMEOUT'), 'TIMEOUT'),
                (WireResponse(401, b'private-error-sentinel', 'req_failed'), 'HTTP_ERROR'),
                (WireResponse(500, response().body, 'req_failed'), 'HTTP_ERROR'),
                (WireResponse(200, b'private-invalid-json', 'req_failed'), 'INVALID_OUTPUT')):
            with self.subTest(status=status):
                obs = parse(wire)
                self.assertEqual(obs['protocol'], 'fi.provider-observation.v2')
                self.assertEqual(obs['status'], status)
                self.assertEqual(obs['request_id'], 'req_failed')
                self.assertIsNone(obs['response_id'])
                self.assertEqual(obs['generation_reported'], MISSING)
                self.assertNotIn('private-', json.dumps(obs))

    def test_metadata_survives_output_rejection_without_changing_output_validation(self):
        for status in ('in_progress', 'completed'):
            data = payload(); data['status'] = status
            data['output'] = [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{}'}]}]
            obs = parse(response(data))
            self.assertEqual(obs['status'], 'INCOMPLETE' if status == 'in_progress' else 'INVALID_OUTPUT')
            self.assertEqual(obs['generation_reported'], PRESENT)
            self.assertEqual(obs['response_id'], 'resp_metadata_fixture')
            self.assertIsNone(obs['output'])


class ProviderMetadataWorkerTests(unittest.TestCase):
    def run_response(self, data):
        repo = MetadataRepo(); wire = RecordingWire(response(data))
        result = run_once(repo, 'exp', wire, {'OPENAI_API_KEY': 'synthetic'})
        return repo, wire, result

    def test_request_bytes_for_both_shapes_are_identical_to_pre_change_fixtures(self):
        cases = ((None, [400000,600000], '1cf0e4606264df084882b149e8ff8d7df4225b101bc5b17015a57dec6bab5bc2'),
            (['3.0','3.5','4.0','4.5'], [200000]*5, 'e312aeb7f2c3ba6db8ff62ec77e8d531b5c96d31b819b93d378468f1d0e52a36'))
        for edges, probabilities, expected_sha in cases:
            repo = MetadataRepo(edges)
            data = payload()
            data['output'] = strict_json(contract.wire(probabilities).body)['output']
            wire = RecordingWire(response(data))
            repo.supports_observation_v2 = Mock(return_value=True)
            with self.subTest(edges=edges):
                result = run_once(repo, 'exp', wire, {'OPENAI_API_KEY': 'synthetic'})
                self.assertEqual(hashlib.sha256(repo.prepared_body.encode()).hexdigest(), expected_sha)
                self.assertEqual(wire.sent_body, repo.prepared_body)
                self.assertEqual(repo.prepared_body, build_request(repo.lease['entry'], repo.packet))
                repo.supports_observation_v2.assert_called_once_with()
                self.assertEqual(repo.events, ['claim','context','prepare','observe','succeed'])
                self.assertEqual(result['status'], 'OK')
                self.assertEqual(result['generation_contract_status'], 'MATCH')
                self.assertEqual(result['request_id'], 'req_metadata_fixture')
                self.assertEqual(result['response_id'], 'resp_metadata_fixture')
                self.assertEqual(result['model_reported'], 'gpt-6.1-terra')
                self.assertEqual(result['generation_reported'], PRESENT)
                self.assertEqual(repo.obs['protocol'], 'fi.provider-observation.v2')
                self.assertEqual(wire.calls, 1)
                self.assertIsNone(result['actual_cost'])
                self.assertIsNone(repo.actual)

    def test_missing_invalid_and_mismatched_parameters_do_not_relabel_output_ok(self):
        cases = []
        data = payload(); del data['reasoning']['mode']; cases.append((data,'MISSING'))
        data = payload(); data['service_tier'] = 'private-value-sentinel'; cases.append((data,'INVALID'))
        data = payload(); data['reasoning']['effort'] = 'high'; cases.append((data,'MISMATCH'))
        data = payload(); data['reasoning']['mode'] = 'pro'; cases.append((data,'MISMATCH'))
        data = payload(); data['service_tier'] = 'priority'; cases.append((data,'MISMATCH'))
        data = payload(); data['model'] = 'other-model'; cases.append((data,'MISMATCH'))
        data = payload(); del data['model']; cases.append((data,'MISSING'))
        data = payload(); data.update(model='other-model',reasoning=None); cases.append((data,'MISMATCH'))
        data = payload(); data.update(reasoning=None,service_tier='priority'); cases.append((data,'MISMATCH'))
        data = payload(); data.update(reasoning=None,service_tier='private-value-sentinel'); cases.append((data,'INVALID'))
        for data, expected in cases:
            with self.subTest(expected=expected, keys=list(data)):
                repo, wire, result = self.run_response(data)
                self.assertEqual(result['generation_contract_status'], expected)
                self.assertEqual(result['status'], 'OK')
                self.assertEqual(repo.events[-2:], ['observe','succeed'])
                self.assertIsNone(repo.actual)
                self.assertEqual(wire.calls, 1)
                self.assertNotIn('private-value-sentinel', json.dumps(result))

    def test_missing_capability_never_prepares_or_sends(self):
        for capability in ('absent', None, False, 1, 'true'):
            repo = MetadataRepo(); wire = RecordingWire(response())
            if capability == 'absent':
                runner_repo = SimpleNamespace(**{name: getattr(repo,name) for name in
                    ('claim','context','prepare','observe','succeed','fail')})
            else:
                runner_repo = repo
                repo.supports_observation_v2 = Mock(return_value=capability)
            with self.subTest(capability=capability):
                result = run_once(runner_repo, 'exp', wire, {'OPENAI_API_KEY': 'synthetic'})
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['reason'], 'OBSERVATION_V2_MIGRATION_REQUIRED')
                self.assertEqual(repo.events, ['claim','fail'])
                self.assertEqual(repo.failure, ('OBSERVATION_V2_MIGRATION_REQUIRED',0,False))
                self.assertEqual(wire.calls, 0)
                self.assertIsNone(repo.prepared_body)

    def test_capability_read_failure_cannot_dispatch(self):
        repo = MetadataRepo(); wire = RecordingWire(response())
        repo.supports_observation_v2 = Mock(side_effect=ConnectionError('synthetic unavailable'))
        with self.assertRaises(ConnectionError):run_once(repo,'exp',wire,{'OPENAI_API_KEY':'synthetic'})
        self.assertEqual(repo.events, ['claim'])
        self.assertEqual(wire.calls, 0)

    def test_legacy_worker_does_not_probe_v2_or_change_observation_shape(self):
        repo = MetadataRepo(); del repo.lease['entry']['parameters']['generation']
        repo.supports_observation_v2 = Mock(side_effect=AssertionError('legacy must not probe'))
        wire = RecordingWire(response())
        result = run_once(repo,'exp',wire,{'OPENAI_API_KEY':'synthetic'})
        self.assertEqual(repo.obs['protocol'], 'fi.provider-observation.v1')
        self.assertEqual(repo.obs['request_id'], 'resp_metadata_fixture')
        self.assertNotIn('generation_contract_status', result)
        self.assertNotIn('response_id', result)
        self.assertNotIn('generation_reported', result)
        self.assertEqual(result['status'], 'OK')
        repo.supports_observation_v2.assert_not_called()

    def test_failed_wire_keeps_unknown_cost_and_missing_metadata(self):
        repo = MetadataRepo()
        wire = RecordingWire(WireResponse(401,b'private-error-sentinel','req_failed'))
        result = run_once(repo,'exp',wire,{'OPENAI_API_KEY':'synthetic'})
        self.assertEqual(result['status'], 'HTTP_ERROR')
        self.assertEqual(result['http_status'], 401)
        self.assertEqual(result['generation_contract_status'], 'MISSING')
        self.assertEqual(result['request_id'], 'req_failed')
        self.assertIsNone(result['response_id'])
        self.assertEqual(result['generation_reported'], MISSING)
        self.assertEqual(repo.failure, ('HTTP_ERROR',None,False))
        self.assertEqual(repo.events[-2:], ['observe','fail'])
        self.assertEqual(wire.calls, 1)
        self.assertNotIn('private-error-sentinel', json.dumps(result))
