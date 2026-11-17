"""Real PostgreSQL restart checkpoint for P0-04--07 tables; hash-only output."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import psycopg
from psycopg import sql

TABLES=('commitment_batches','batch_records','batch_seals','anchor_requests','anchor_replies','anchor_verifications',
        'outcome_evidence','resolution_proposals','resolution_heads','resolution_confirmations','score_runs','score_rows','source_captures')

def snapshot():
    dsn=os.environ.get('FI_TEST_DATABASE_URL')
    if not dsn:raise RuntimeError('real PostgreSQL required')
    output={}
    with psycopg.connect(dsn) as c:
        c.execute("SET TIME ZONE 'UTC'")
        for table in TABLES:
            rows=c.execute(sql.SQL('SELECT row_to_json(t)::text FROM fi.{} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table))).fetchall()
            output[table]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps([r[0] for r in rows],ensure_ascii=False).encode()).hexdigest()}
    if any(not row['rows'] for row in output.values()):raise RuntimeError('empty table is not restart acceptance')
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['write','verify']);p.add_argument('file',type=Path);args=p.parse_args()
    actual=snapshot()
    if args.mode=='write':args.file.write_text(json.dumps(actual,sort_keys=True),encoding='utf-8');print('Saved 13-table delivery checkpoint; hashes only.')
    elif actual!=json.loads(args.file.read_text(encoding='utf-8')):raise RuntimeError('delivery records changed across server restart')
    else:print('PASS: 13 delivery tables survived actual PostgreSQL restart. Events/providers/TSA are test fixtures, not live acceptance.')
