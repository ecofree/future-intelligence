"""Reviewed BLS draft v2. An absent API value alone never proves an unknown result.

Snapshots are submitted to the database admission ledger and independently
reviewed before a formal run. This module only prepares a candidate.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

FAMILIES = frozenset({'CPI', 'PPI', 'EMPLOYMENT_SITUATION', 'JOLTS'})


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('explicit timezone required')
    return result.astimezone(timezone.utc)


def event_cluster(family: str, release_at: str) -> str:
    if family not in FAMILIES:
        raise ValueError('reviewed release family required')
    # Different series in the same official release share one sampling unit.
    return 'BLS:' + family + ':' + aware(release_at).isoformat(timespec='seconds')


def strict_document(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 2_000_000:
        raise ValueError('bounded original document bytes required')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError('nonfinite JSON value')
    result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError('JSON object required')
    return result


def draft_bls_question(template: dict, calendar_document: bytes, target_document: bytes,
                       *, now: datetime | None = None) -> dict:
    from future_intelligence.delivery.operations import bls_value
    from future_intelligence.questions.spec import validate_spec
    fields = {'series','year','period','release_family','forecast_deadline','outcome_not_before',
              'resolution_deadline','original_timezone','calendar_reference','bin_edges','unit','review_note'}
    if not isinstance(template, dict) or set(template) != fields:
        raise ValueError('exact reviewed template v2 required')
    if not isinstance(calendar_document, bytes) or not 1 <= len(calendar_document) <= 2_000_000:
        raise ValueError('calendar snapshot bytes required')
    t = template
    if not isinstance(t['series'], str) or not re.fullmatch('[A-Z0-9]{4,30}', t['series']):
        raise ValueError('exact series required')
    if type(t['year']) is not int or not 1900 <= t['year'] <= 2200 or not re.fullmatch('M(0[1-9]|1[0-2])', t['period']):
        raise ValueError('exact target period required')
    reference = urlsplit(t['calendar_reference'])
    if (reference.scheme != 'https' or reference.hostname not in {'bls.gov','www.bls.gov'}
        or reference.username or reference.password or reference.port not in (None,443)
        or reference.fragment or reference.query):
        raise ValueError('unambiguous official calendar URL required')
    if not isinstance(t['review_note'], str) or not 10 <= len(t['review_note'].strip()) <= 2000:
        raise ValueError('explicit calendar review required')
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError('observation timezone required')
    cutoff, release, resolution = map(aware, [t['forecast_deadline'],t['outcome_not_before'],t['resolution_deadline']])
    if not observed < cutoff <= release <= resolution:
        raise ValueError('prospective window required')
    document = strict_document(target_document)
    if bls_value(document,t['series'],t['year'],t['period']) is not None:
        raise ValueError('target already present; prospective admission rejected')
    cluster = event_cluster(t['release_family'],t['outcome_not_before'])
    calendar_hash = hashlib.sha256(calendar_document).hexdigest()
    target = f"{t['series']}:{t['year']}:{t['period']}"
    spec = validate_spec(dict(title='First official release: BLS:'+target,
        event_definition='Value of the exact BLS series and reference period '+target,
        domain='MACRO',track='FAST',question_type='binned_numeric',event_cluster=cluster,
        original_timezone=t['original_timezone'],forecast_deadline=t['forecast_deadline'],
        outcome_not_before=t['outcome_not_before'],resolution_deadline=t['resolution_deadline'],
        resolution_rule='Use the original official release, not a silently revised time-series value. Calendar snapshot '+calendar_hash,
        data_revision_policy='FIRST_RELEASE',unit=t['unit'],bin_edges=t['bin_edges']))
    return {'protocol':'fi.bls-draft.v2','status':'CANDIDATE_REQUIRES_INDEPENDENT_REVIEW','spec':spec,
            'calendar_sha256':calendar_hash,'target_document_sha256':hashlib.sha256(target_document).hexdigest(),
            'known_answer_check':'NOT_FOUND_IN_THIS_SNAPSHOT_ONLY','review_note':t['review_note']}
