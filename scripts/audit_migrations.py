"""Read-only migration inventory; never applies, renames or repairs a migration.

Read an explicitly named DSN environment variable. Report hashes and migration
names, never credentials, file contents, database error strings or private paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re


def compare_manifests(packaged: dict[str,str], applied: dict[str,str]) -> dict:
    """A differing checksum is a stop, not permission to overwrite history."""
    for manifest in (packaged,applied):
        if not isinstance(manifest,dict): raise ValueError('invalid manifest')
        for name,digest in manifest.items():
            if (not isinstance(name,str) or not re.fullmatch(r'[0-9]{4}_[A-Za-z0-9_.-]+\.sql',name)
                    or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)):
                raise ValueError('invalid migration identity')
    if not packaged: raise ValueError('empty packaged migration manifest')
    unknown=sorted(set(applied)-set(packaged))
    mismatches=[{'version':n,'packaged_sha256':packaged[n],'applied_sha256':applied[n]}
                for n in sorted(set(packaged)&set(applied)) if packaged[n]!=applied[n]]
    pending=sorted(set(packaged)-set(applied))
    prior=max(applied,default='')
    out_of_order=[n for n in pending if n<prior]
    duplicate_prefixes=sorted(k for k,v in Counter(n.split('_',1)[0] for n in packaged).items() if v>1)
    status=('BLOCKED_HISTORY_MISMATCH' if unknown or mismatches else
            'REVIEW_REQUIRED_ORDER' if out_of_order else
            'PENDING_FORWARD_MIGRATIONS' if pending else 'MATCH')
    return {'protocol':'fi.migration-audit.v1','status':status,'read_only':True,
            'packaged':[{'version':n,'sha256':h} for n,h in sorted(packaged.items())],
            'applied':[{'version':n,'sha256':h} for n,h in sorted(applied.items())],
            'unknown_applied':unknown,'checksum_mismatches':mismatches,'pending':pending,
            'pending_before_last_applied':out_of_order,
            'duplicate_numeric_prefixes':duplicate_prefixes,
            'duplicate_prefix_note':'Advisory only: full filenames identify migrations; inspect recorded order.',
            'historical_order_status':'NOT_CHECKED',
            'repair_performed':False,'production_acceptance':False}


def compare_history(packaged: dict[str, str], records: list[tuple[str, str, datetime]]) -> dict:
    """Compare hashes and recorded order without treating DB time as an attestation.

    Equal timestamps do not establish which migration ran first. A later
    reconciliation filename alone does not prove a prior inversion was repaired.
    Both cases require review; neither authorizes changing historical metadata.
    """
    applied = {}
    groups = {}
    for row in records:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise ValueError('invalid applied migration record')
        name, digest, moment = row
        if not isinstance(name, str) or name in applied:
            raise ValueError('invalid or duplicate applied migration identity')
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError('timezone-aware applied timestamp required')
        applied[name] = digest
        groups.setdefault(moment.astimezone(timezone.utc), []).append(name)
    result = compare_manifests(packaged, applied)
    inversions, ties, chronology = [], [], []
    greatest_prior = None
    for moment, names in sorted(groups.items()):
        names = sorted(names)
        chronology.extend({'version': name, 'applied_at': moment.isoformat()} for name in names)
        if len(names) > 1:
            ties.append({'applied_at': moment.isoformat(), 'versions': names})
        if greatest_prior is not None:
            prior_name, prior_time = greatest_prior
            for name in names:
                if name < prior_name:
                    inversions.append({'earlier_version': prior_name,
                                       'earlier_applied_at': prior_time.isoformat(),
                                       'later_version': name,
                                       'later_applied_at': moment.isoformat()})
        # Compare only distinct time groups; alphabetical tie-breaking is not evidence.
        if greatest_prior is None or names[-1] > greatest_prior[0]:
            greatest_prior = (names[-1], moment)
    result.update(
        applied_order=chronology,
        applied_order_inversions=inversions,
        applied_timestamp_ties=ties,
        historical_order_status=(
            'NONCANONICAL_RECORDED_ORDER' if inversions else
            'INDETERMINATE_TIED_TIMESTAMPS' if ties else
            'CONSISTENT_RECORDED_TIMESTAMPS' if records else 'EMPTY_HISTORY'),
        historical_order_note=(
            'Based only on recorded database timestamps, not independent execution proof. '
            'Clock changes, equal timestamps and deliberate reconciliation require review. '
            'Matching files and hashes do not prove the effective schema or safe deployment.'),
    )
    if (inversions or ties) and result['status'] != 'BLOCKED_HISTORY_MISMATCH':
        result['status'] = 'REVIEW_REQUIRED_ORDER'
    return result


def audit(dsn: str, migration_directory=None) -> dict:
    import psycopg
    root=Path(migration_directory) if migration_directory is not None else files('future_intelligence.db').joinpath('migrations')
    packaged={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.iterdir() if p.name.endswith('.sql') and p.is_file()}
    # Validate before connecting. No source files or historical checksums change.
    compare_manifests(packaged,{})
    with psycopg.connect(dsn) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        connection.execute("SET LOCAL statement_timeout='10s'")
        exists=connection.execute("SELECT to_regclass('fi_meta.schema_migrations') IS NOT NULL").fetchone()[0]
        records=connection.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations ORDER BY applied_at,version').fetchall() if exists else []
        result=compare_history(packaged,records)
        result['metadata_present']=exists
    return result


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn-env',default='FI_MIGRATION_DATABASE_URL')
    parser.add_argument('--migrations-dir',help='Optional explicit local SQL directory; never printed.')
    args=parser.parse_args(argv)
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}',args.dsn_env): parser.error('invalid environment variable name')
    dsn=os.environ.get(args.dsn_env)
    if not dsn:
        print(json.dumps({'status':'BLOCKED_MISSING_DSN','read_only':True,'repair_performed':False}));return 2
    try:
        result=audit(dsn,args.migrations_dir)
    except Exception:
        print(json.dumps({'status':'BLOCKED_AUDIT_FAILED','read_only':True,'repair_performed':False}));return 2
    print(json.dumps(result,sort_keys=True,ensure_ascii=False,indent=2))
    return 0 if result['status']=='MATCH' else 2


if __name__=='__main__':raise SystemExit(main())
