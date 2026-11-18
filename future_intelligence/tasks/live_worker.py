"""One controlled LIVE task. No implicit retries or fixture fallback.

A supervisor may schedule invocations, but this command is not a deployed service.
A lost database acknowledgement never authorizes another provider dispatch.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from future_intelligence.providers.compatible import wire_kind
from future_intelligence.providers.live import (
    COMPATIBLE, Blocked, HTTPSOnce, KEYS, WireResponse, build_request, credentials,
    credentials_for, encode, estimated_cost, parse_response, strict_json, validate_entry,
)
from .repository import uuid_text


def load_count_record(path: str | Path) -> dict:
    """Read a bounded private receipt without echoing its contents on failure."""
    try:
        with Path(path).open('rb') as source:
            raw = source.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError
        record = strict_json(raw)
        if type(record) is not dict:
            raise ValueError
        return record
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError('INPUT_COUNT_RECORD_INVALID') from None


def run_once(repo, experiment: str, transport=None, environ=None, *, task_id: str | UUID | None = None,
             input_count_record: dict | None = None) -> dict:
    wire_client = HTTPSOnce() if transport is None else transport
    target = uuid_text(task_id) if task_id is not None else None
    if input_count_record is not None and target is None:
        return {'status': 'BLOCKED', 'reason': 'INPUT_COUNT_REQUIRES_EXACT_TASK'}
    lease = repo.claim(experiment, 'LIVE', task_id=target) if target is not None else repo.claim(experiment, 'LIVE')
    if lease is None:
        if target is not None:
            return {'status': 'BLOCKED', 'reason': 'TARGET_TASK_NOT_CLAIMABLE',
                    'requested_task_id': target}
        return {'status': 'NO_CLAIM', 'meaning': 'No eligible work, budget unavailable, or deadline passed.'}
    identity = {'task_id': lease['task_id'], 'attempt_id': lease['attempt_id'], 'run_kind': 'LIVE'}
    try:
        if target is not None:
            try:
                matches = uuid_text(lease['task_id']) == target
            except (ValueError, TypeError, AttributeError):
                matches = False
            if not matches:
                raise Blocked('CLAIMED_TASK_MISMATCH')
        entry = validate_entry(lease['entry'], lease['cap_microusd'])
        key = credentials_for(entry, environ)
        context = repo.context(lease)
        body = build_request(entry, context)
        if input_count_record is not None:
            from future_intelligence.providers.input_count import validate_count_record
            try:
                count = validate_count_record(body.encode('utf-8'), input_count_record,
                    entry['parameters']['input_token_ceiling'], datetime.now(timezone.utc))
            except ValueError:
                raise Blocked('INPUT_COUNT_VALIDATION_FAILED') from None
            identity['input_count'] = {
                'status': 'VALIDATED_LOCAL_RECORD', 'input_tokens': count,
                'record_sha256': hashlib.sha256(encode(input_count_record)).hexdigest(),
                'independent_time_proof': False,
            }
        lease_end = datetime.fromisoformat(lease['lease_until'].replace('Z', '+00:00'))
        remaining = (lease_end-datetime.now(timezone.utc)).total_seconds()
        if remaining < entry['parameters']['timeout_seconds'] + 10:
            raise Blocked('LEASE_TOO_SHORT')
    except Blocked as exc:
        repo.fail(lease, str(exc), 0, False)  # Proven no network dispatch.
        return {**identity, 'status': 'BLOCKED', 'reason': str(exc)}
    # DB failure here propagates sanitized by the CLI; no send happens without True.
    if repo.prepare(lease, body) is not True:
        return {**identity, 'status': 'NO_REDISPATCH', 'reason': 'CALL_MARKER_ALREADY_EXISTS'}
    try:
        # Lock waits during prepare must not spend an already exhausted lease.
        # A committed intent still remains conservatively unresolved for recovery.
        remaining = (lease_end-datetime.now(timezone.utc)).total_seconds()
        if remaining < entry['parameters']['timeout_seconds'] + 5:
            wire = WireResponse(None, b'', error_code='TIMEOUT')
        else:
            extra = {'endpoint': entry['parameters']['endpoint']} if entry['provider'] == COMPATIBLE else {}
            wire = wire_client.send(entry['provider'], entry['model_id'], body, key,
                                    entry['parameters']['timeout_seconds'], **extra)
    except Exception:
        # The boundary is conservatively unknown after committed dispatch intent.
        wire = WireResponse(None, b'', error_code='TRANSPORT_ERROR')
    finally:
        key = ''
    spec = context['question_spec']
    categories = 2 if spec['question_type'] == 'binary' else len(spec['bin_edges'])+1
    ids = {x['id'] for x in context['evidence']['items']}
    obs = parse_response(wire_kind(entry), wire, categories, ids)
    repo.observe(lease, obs)  # Persist late usage evidence even if lease has expired.
    if obs['status'] == 'OK':
        receipt = repo.succeed(lease, obs['output']['probabilities_ppm'], obs['response_sha256'], None)
    else:
        receipt = repo.fail(lease, obs['status'], None, False)
    http_status = obs['http_status']
    return {**identity, 'status': obs['status'], 'receipt': receipt,
            'http_status': http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
            'estimated_microusd': estimated_cost(entry, obs),
            'cost_kind': 'QUOTE_ESTIMATE_NOT_INVOICE', 'actual_cost': None,
            'external_timestamp_status': 'NOT_REQUESTED'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment', nargs='?')
    parser.add_argument('--task', type=UUID, help='Claim exactly this task; never fall back to another question.')
    parser.add_argument('--input-count-record', type=Path, help='Validate this official input-count receipt before sending; requires --task.')
    parser.add_argument('--preflight', action='store_true', help='Check credential presence only; never prints values or sends HTTP.')
    args = parser.parse_args()
    if args.input_count_record is not None and (args.task is None or args.preflight):
        parser.error('--input-count-record requires --task and cannot be used with --preflight')
    if args.preflight:
        readiness = {}
        for provider in KEYS:
            try:
                credentials(provider)
                readiness[provider] = 'PRESENT_NOT_VALIDATED'
            except Blocked:
                readiness[provider] = 'BLOCKED_MISSING_OR_INVALID_CREDENTIAL'
        print(json.dumps({'credential_presence': readiness, 'real_api_verified': False}))
        return 0 if all(x == 'PRESENT_NOT_VALIDATED' for x in readiness.values()) else 2
    dsn = os.environ.get('FI_WORKER_DATABASE_URL')
    if not dsn or not args.experiment:
        print(json.dumps({'status': 'BLOCKED', 'reason': 'WORKER_DSN_AND_EXPERIMENT_REQUIRED'}))
        return 2
    try:
        count_record = load_count_record(args.input_count_record) if args.input_count_record is not None else None
        from future_intelligence.evidence.repository import EvidenceRepository
        result = run_once(EvidenceRepository(dsn), args.experiment, task_id=args.task,
                          input_count_record=count_record)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in ('OK', 'NO_CLAIM') else 2
    except Exception:
        # Never print database DSNs, headers, vendor response bodies or secrets.
        print(json.dumps({'status': 'BLOCKED_OR_UNCERTAIN',
                          'reason': 'PERSISTENCE_OR_STATE_ERROR_NO_AUTOMATIC_REDISPATCH'}))
        return 3


if __name__ == '__main__':
    sys.exit(main())
