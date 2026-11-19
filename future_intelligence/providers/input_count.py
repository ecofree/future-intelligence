"""Offline mapping and receipt checks for the explicit official Terra request.

This module sends no HTTP and writes no ledger. Its caller must check a record
before dispatching the exact source request. The record's hashes and local
timestamps are consistency checks, not an independent signature or time proof.
No accounting outcome is inferred here for either counting or generation.

Count records use fi.openai-input-count.v1 and exactly RECORD_FIELDS below.
Both ISO timestamps require offsets; the whole count must precede ``now`` and
have started no more than 24 hours earlier. ``request_id`` is the API request
identifier (req_...), not a Responses object identifier (resp_...). A syntactic
check cannot establish that a caller supplied a genuine upstream receipt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re

from .live import PROMPT_V2, encode, output_schema_v2, strict_json
from future_intelligence.questions.spec import answer_contract


PROTOCOL = 'fi.openai-input-count.v1'
ENDPOINT = 'https://api.openai.com/v1/responses/input_tokens'
MAX_RECORD_AGE = timedelta(hours=24)
_MAX_BODY_BYTES = 2 * 1024 * 1024
_SOURCE_FIELDS = frozenset({'input', 'instructions', 'max_output_tokens', 'model',
                            'reasoning', 'service_tier', 'store', 'stream', 'text', 'tools'})
_COUNT_FIELDS = frozenset({'input', 'instructions', 'model', 'reasoning', 'text', 'tools'})
RECORD_FIELDS = frozenset({'protocol', 'endpoint', 'source_request_sha256',
                           'count_request_sha256', 'response_body', 'response_sha256',
                           'http_status', 'request_id', 'started_at', 'finished_at'})
_HASH = re.compile(r'[0-9a-f]{64}')
_REQUEST_ID = re.compile(r'req_[A-Za-z0-9_-]{1,196}')
_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                   r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})')


def build_count_request(body: bytes) -> bytes:
    """Keep all six supported input fields; omit only four known controls.

    Reject other models, legacy/nonexplicit configurations and unknown fields.
    Do not normalize the source request bytes or its embedded input string.
    """
    try:
        if type(body) is not bytes or not 0 < len(body) <= _MAX_BODY_BYTES:
            raise ValueError
        source = strict_json(body.decode('utf-8'))
        if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
            raise ValueError
        if (source['model'] != 'gpt-6.1-terra' or source['instructions'] != PROMPT_V2
                or source['service_tier'] != 'default'
                or source['store'] is not False or source['stream'] is not False
                or source['tools'] != []
                or type(source['max_output_tokens']) is not int
                or not 128 <= source['max_output_tokens'] <= 16384
                or encode(source['reasoning']) != encode({'effort': 'medium', 'mode': 'standard'})
                or not isinstance(source['input'], str)):
            raise ValueError
        data = strict_json(source['input'])
        if (not isinstance(data, dict)
                or set(data) != {'protocol', 'question_spec', 'evidence', 'evidence_sha256', 'answer_contract'}
                or data['protocol'] != 'fi.model-input.v2'):
            raise ValueError
        contract = answer_contract(data['question_spec'])
        if encode(data['answer_contract']) != encode(contract):
            raise ValueError
        expected_text = {'format': {'type': 'json_schema', 'name': 'forecast', 'strict': True,
                                    'schema': output_schema_v2(contract['category_count'], 'openai')}}
        if encode(source['text']) != encode(expected_text):
            raise ValueError
        # Encoding also rejects overflowed JSON numbers and invalid surrogates.
        encode(data)
        return encode({key: source[key] for key in _COUNT_FIELDS})
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        raise ValueError('INPUT_COUNT_SOURCE_INVALID') from None


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def validate_count_record(body: bytes, record: dict, ceiling: int, now: datetime) -> int:
    """Return an eligible positive count, otherwise a fixed sanitized error.

    An ordinary Responses HTTP 200 plus ``usage`` is never a count receipt.
    Changing even a generation-only field invalidates the source-body binding.
    This function does not verify transport provenance or enforce a DB gate.
    """
    count_body = build_count_request(body)
    try:
        if (type(record) is not dict or set(record) != RECORD_FIELDS
                or record['protocol'] != PROTOCOL or record['endpoint'] != ENDPOINT
                or type(record['http_status']) is not int or record['http_status'] != 200
                or not isinstance(record['request_id'], str)
                or not _REQUEST_ID.fullmatch(record['request_id'])
                or type(ceiling) is not int or not 1 <= ceiling <= 200000
                or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None):
            raise ValueError
        for key in ('source_request_sha256', 'count_request_sha256', 'response_sha256'):
            if not isinstance(record[key], str) or not _HASH.fullmatch(record[key]):
                raise ValueError
        if not isinstance(record['response_body'], str):
            raise ValueError
        response_bytes = record['response_body'].encode('utf-8')
        if not 0 < len(response_bytes) <= _MAX_BODY_BYTES:
            raise ValueError
        response = strict_json(record['response_body'])
        if (not isinstance(response, dict) or set(response) != {'object', 'input_tokens'}
                or response['object'] != 'response.input_tokens'
                or type(response['input_tokens']) is not int or response['input_tokens'] <= 0):
            raise ValueError
        started = _timestamp(record['started_at'])
        finished = _timestamp(record['finished_at'])
        moment = now.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        raise ValueError('INPUT_COUNT_RECORD_INVALID') from None

    if (record['source_request_sha256'] != hashlib.sha256(body).hexdigest()
            or record['count_request_sha256'] != hashlib.sha256(count_body).hexdigest()
            or record['response_sha256'] != hashlib.sha256(response_bytes).hexdigest()):
        raise ValueError('INPUT_COUNT_BINDING_MISMATCH')
    if not started <= finished <= moment:
        raise ValueError('INPUT_COUNT_TIME_INVALID')
    if moment - started > MAX_RECORD_AGE:
        raise ValueError('INPUT_COUNT_STALE')
    if response['input_tokens'] > ceiling:
        raise ValueError('INPUT_COUNT_LIMIT_EXCEEDED')
    return response['input_tokens']
