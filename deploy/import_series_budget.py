"""Open a sealed replacement fee ledger from the verified private handoff."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import psycopg
from psycopg.types.json import Jsonb

from future_intelligence.runtime.secrets import read_secret
from future_intelligence.delivery.timestamps import trust_profile_from_dict


def checked_file(path: Path, expected: str) -> None:
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('a separately established SHA-256 is required')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('handoff digest mismatch')


def import_opening(dsn: str, manifest: Path, expected: str) -> dict:
    # Parse the same bytes that were authenticated. The archives stay private and unopened.
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('opening manifest digest mismatch')
    record = json.loads(raw)
    root = manifest.parent.resolve()
    artifacts = record['artifacts']
    if not artifacts or record['handoff_format_version'] != 1:
        raise ValueError('unsupported handoff')
    for artifact in artifacts:
        path = (root / artifact['path']).resolve()
        if not path.is_relative_to(root) or path.stat().st_size != artifact['size_bytes']:
            raise ValueError('invalid handoff artifact')
        checked_file(path, artifact['sha256'])
    budget = record['budget']
    proposed = budget['formal_candidate_only']
    holds = budget['hold_breakdown_microusd']
    if (any(type(x) is not int or x < 0 for x in holds.values())
            or sum(holds.values()) != budget['held_unknown_microusd']
            or sum(holds.values()) + budget['uncommitted_microusd'] != budget['limit_microusd']
            or proposed['not_yet_reserved'] is not True or budget['automatic_retry_authorized'] is not False
            or budget['may_release_unknown_without_billing_evidence'] is not False):
        raise ValueError('inconsistent series budget evidence')
    with psycopg.connect(dsn) as conn:
        conn.execute('SELECT fi.open_series_budget(%s,%s,%s,%s,%s,%s)',
                     (expected, record['state_verified_at'], Jsonb(holds), budget['limit_microusd'],
                      proposed['calls'], proposed['per_call_upper_bound_microusd']))
        row = conn.execute('SELECT to_jsonb(s) FROM fi.series_budget_status s').fetchone()[0]
    return row


def pin_site_trust(dsn: str, profile_path: Path, review_path: Path) -> str:
    profile = json.loads(profile_path.read_bytes())
    trust_profile_from_dict(profile)
    evidence = review_path.read_bytes()
    if not evidence:
        raise ValueError('signed-list review evidence required')
    review_hash = hashlib.sha256(evidence).hexdigest()
    with psycopg.connect(dsn) as conn:
        conn.execute("""INSERT INTO fi.series_trust(singleton,profile,source_review_sha256) VALUES(true,%s,%s)
                        ON CONFLICT (singleton) DO NOTHING""",(Jsonb(profile),review_hash))
        row = conn.execute('SELECT profile,source_review_sha256 FROM fi.series_trust').fetchone()
        if row != (profile,review_hash):
            raise ValueError('site trust already bound to different material')
    return review_hash


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--trust-profile', type=Path)
    parser.add_argument('--trust-review', type=Path)
    args = parser.parse_args()
    if (args.trust_profile is None) != (args.trust_review is None):
        parser.error('supply the reviewed profile and its evidence together')
    dsn = read_secret('FI_MIGRATION_DATABASE_URL')
    result = import_opening(dsn,args.manifest,args.sha256)
    if args.trust_profile:
        result['trust_review_sha256'] = pin_site_trust(dsn,args.trust_profile,args.trust_review)
    print(json.dumps(result))
