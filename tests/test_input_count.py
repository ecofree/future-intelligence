"""Synthetic count receipts only: no model, token-count, or network requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

import test_forecast_contract as fixture
from future_intelligence.providers.input_count import (
    ENDPOINT, PROTOCOL, build_count_request, validate_count_record,
)
from future_intelligence.providers.live import build_request, encode, strict_json, terra_generation_profile


NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def source_body(edges=None):
    entry = fixture.entry()
    entry['model_id'] = 'gpt-6.1-terra'
    entry['parameters']['generation'] = terra_generation_profile()
    return build_request(entry, fixture.context(edges)).encode('utf-8')


def record_for(body, *, response=None):
    response_body = encode({'object': 'response.input_tokens', 'input_tokens': 2070}
                           if response is None else response).decode('utf-8')
    return {'protocol': PROTOCOL, 'endpoint': ENDPOINT,
            'source_request_sha256': hashlib.sha256(body).hexdigest(),
            'count_request_sha256': hashlib.sha256(build_count_request(body)).hexdigest(),
            'response_body': response_body,
            'response_sha256': hashlib.sha256(response_body.encode('utf-8')).hexdigest(),
            'http_status': 200, 'request_id': 'req_SYNTHETIC_count_not_a_live_receipt',
            'started_at': (NOW - timedelta(seconds=2)).isoformat(),
            'finished_at': (NOW - timedelta(seconds=1)).isoformat()}


class InputCountTests(unittest.TestCase):
    def test_retains_complete_binary_and_five_category_input(self):
        for edges in (None, ['3.0', '3.5', '4.0', '4.5']):
            with self.subTest(edges=edges):
                body = source_body(edges)
                original = strict_json(body)
                counted = strict_json(build_count_request(body))
                kept = {'input', 'instructions', 'model', 'reasoning', 'text', 'tools'}
                self.assertEqual(set(counted), kept)
                self.assertEqual(counted, {key: original[key] for key in kept})
                self.assertEqual(counted['reasoning'], {'effort': 'medium', 'mode': 'standard'})
                schema = counted['text']['format']['schema']['properties']['probabilities_ppm']
                self.assertEqual((schema['minItems'], schema['maxItems']), (2, 2) if edges is None else (5, 5))
                self.assertTrue(counted['text']['format']['strict'])
                self.assertEqual(validate_count_record(body, record_for(body), 16384, NOW), 2070)

    def test_rejects_other_models_legacy_or_nonexplicit_profile(self):
        original = strict_json(source_body())
        changes = [('model', 'gpt-6.2-sol'), ('instructions', 'different prompt'),
                   ('reasoning', {'effort': 'medium'}),
                   ('reasoning', {'effort': 'medium', 'mode': 'pro'}),
                   ('service_tier', 'priority'), ('store', True), ('stream', True),
                   ('tools', [{'type': 'web_search'}]), ('max_output_tokens', True)]
        for field, value in changes:
            altered = deepcopy(original); altered[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_SOURCE_INVALID$'):
                build_count_request(encode(altered))
        for field in ('reasoning', 'service_tier'):
            altered = deepcopy(original); del altered[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                build_count_request(encode(altered))

    def test_unknown_generation_fields_are_not_silently_dropped(self):
        for field, value in (('previous_response_id', 'resp_prior'), ('conversation', 'conv_prior'),
                             ('temperature', 1), ('future_input_control', 'not ignored')):
            altered = strict_json(source_body()); altered[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_SOURCE_INVALID$'):
                build_count_request(encode(altered))

    def test_generation_only_change_invalidates_source_binding(self):
        body = source_body(); record = record_for(body)
        altered = strict_json(body); altered['max_output_tokens'] = 8192
        changed = encode(altered)
        self.assertEqual(build_count_request(body), build_count_request(changed))
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_BINDING_MISMATCH$'):
            validate_count_record(changed, record, 16384, NOW)

    def test_original_source_bytes_are_bound_without_canonical_replacement(self):
        body = source_body(); record = record_for(body)
        reformatted = json.dumps(strict_json(body), indent=2).encode('utf-8')
        self.assertEqual(build_count_request(body), build_count_request(reformatted))
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_BINDING_MISMATCH$'):
            validate_count_record(reformatted, record, 16384, NOW)

    def test_changed_input_or_schema_cannot_reuse_a_receipt(self):
        body = source_body(); record = record_for(body)
        changed = strict_json(body); data = strict_json(changed['input'])
        data['evidence']['items'][0]['text'] = 'Different synthetic evidence.'
        changed['input'] = encode(data).decode('utf-8')
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_BINDING_MISMATCH$'):
            validate_count_record(encode(changed), record, 16384, NOW)
        changed = strict_json(body)
        changed['text']['format']['schema']['properties']['probabilities_ppm']['maxItems'] = 5
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_SOURCE_INVALID$'):
            validate_count_record(encode(changed), record, 16384, NOW)

    def test_each_hash_must_match(self):
        body = source_body()
        for field in ('source_request_sha256', 'count_request_sha256', 'response_sha256'):
            record = record_for(body); record[field] = '0' * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_BINDING_MISMATCH$'):
                validate_count_record(body, record, 16384, NOW)
        record = record_for(body)
        record['response_body'] = record['response_body'].replace('2070', '2071')
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_BINDING_MISMATCH$'):
            validate_count_record(body, record, 16384, NOW)

    def test_http_error_and_completed_response_usage_are_not_counts(self):
        body = source_body(); record = record_for(body); record['http_status'] = 400
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
            validate_count_record(body, record, 16384, NOW)
        for response in ({'object': 'response', 'usage': {'input_tokens': 2070}},
                         {'object': 'response', 'input_tokens': 2070},
                         {'object': 'response.input_tokens', 'input_tokens': 2070, 'usage': {}}):
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
                validate_count_record(body, record_for(body, response=response), 16384, NOW)

    def test_positive_integer_count_and_ceiling(self):
        body = source_body()
        for count in (True, False, 2070.0, '2070', None, 0, -1):
            record = record_for(body, response={'object': 'response.input_tokens', 'input_tokens': count})
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
                validate_count_record(body, record, 16384, NOW)
        record = record_for(body)
        self.assertEqual(validate_count_record(body, record, 2070, NOW), 2070)
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_LIMIT_EXCEEDED$'):
            validate_count_record(body, record, 2069, NOW)
        for ceiling in (True, 16384.0, '16384', 0, -1, 200001):
            with self.subTest(ceiling=ceiling), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
                validate_count_record(body, record, ceiling, NOW)

    def test_official_endpoint_protocol_request_id_and_closed_record_shape(self):
        body = source_body()
        invalid = [('endpoint', 'https://relay.invalid/v1/responses/input_tokens'),
                   ('endpoint', ENDPOINT + '?proxy=true'), ('protocol', 'other'),
                   ('request_id', 'resp_not_an_api_request_id'), ('request_id', 'req_bad\nheader'),
                   ('request_id', ''), ('http_status', 200.0)]
        for field, value in invalid:
            record = record_for(body); record[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
                validate_count_record(body, record, 16384, NOW)
        for field in record_for(body):
            record = record_for(body); del record[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate_count_record(body, record, 16384, NOW)
        record = record_for(body); record['verified'] = True
        with self.assertRaises(ValueError): validate_count_record(body, record, 16384, NOW)

    def test_timezone_order_and_24_hour_freshness(self):
        body = source_body(); record = record_for(body)
        shanghai = timezone(timedelta(hours=8))
        record['started_at'] = (NOW - timedelta(seconds=2)).astimezone(shanghai).isoformat()
        record['finished_at'] = NOW.isoformat().replace('+00:00', 'Z')
        self.assertEqual(validate_count_record(body, record, 16384, NOW), 2070)
        for field, value, reason in (
                ('started_at', (NOW + timedelta(seconds=1)).isoformat(), 'INPUT_COUNT_TIME_INVALID'),
                ('finished_at', (NOW + timedelta(seconds=1)).isoformat(), 'INPUT_COUNT_TIME_INVALID'),
                ('started_at', (NOW - timedelta(hours=24, microseconds=1)).isoformat(), 'INPUT_COUNT_STALE'),
                ('started_at', '2026-11-18T11:59:58', 'INPUT_COUNT_RECORD_INVALID'),
                ('finished_at', 'not-a-date', 'INPUT_COUNT_RECORD_INVALID')):
            altered = record_for(body); altered[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '^' + reason + '$'):
                validate_count_record(body, altered, 16384, NOW)
        record['started_at'] = (NOW - timedelta(hours=24)).isoformat()
        self.assertEqual(validate_count_record(body, record, 16384, NOW), 2070)
        with self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
            validate_count_record(body, record, 16384, NOW.replace(tzinfo=None))

    def test_duplicate_nonfinite_malformed_json_and_types_are_sanitized(self):
        body = source_body()
        bad_source = [body.decode('utf-8'), b'\xff', b'{}', b'{"private-key":NaN}',
                      body[:-1] + b',"model":"gpt-6.1-terra"}', b'[]']
        for value in bad_source:
            with self.subTest(kind=type(value)), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_SOURCE_INVALID$'):
                build_count_request(value)
        for raw in ('{"object":"response.input_tokens","input_tokens":NaN}',
                    '{"object":"response.input_tokens","input_tokens":1e999}',
                    '{"object":"response.input_tokens","input_tokens":1,"input_tokens":2}',
                    'private-key-not-json', '\ud800'):
            record = record_for(body); record['response_body'] = raw
            with self.subTest(raw=repr(raw)), self.assertRaisesRegex(ValueError, '^INPUT_COUNT_RECORD_INVALID$'):
                validate_count_record(body, record, 16384, NOW)

    def test_inputs_are_not_mutated(self):
        body = source_body(); record = record_for(body); before = deepcopy(record)
        validate_count_record(body, record, 16384, NOW)
        self.assertEqual(record, before)


if __name__ == '__main__':
    unittest.main()
