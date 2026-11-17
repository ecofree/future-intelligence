"""CI-only fingerprints across a real server restart; never prints evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import psycopg
from psycopg import sql

TABLES=('evidence_seals','evidence_packets','provider_requests','provider_observations')


def checkpoint():
    dsn=os.environ.get('FI_TEST_DATABASE_URL')
    if not dsn: raise RuntimeError('FI_TEST_DATABASE_URL required')
    result={}
    with psycopg.connect(dsn) as c:
        c.execute("SET TIME ZONE 'UTC'")
        for table in TABLES:
            rows=c.execute(sql.SQL('SELECT row_to_json(t)::text FROM fi.{} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table))).fetchall()
            if not rows: raise RuntimeError('empty table does not prove persistence: '+table)
            result[table]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps([r[0] for r in rows],ensure_ascii=False).encode()).hexdigest()}
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('mode',choices=['write','verify']); p.add_argument('path',type=Path)
    a=p.parse_args(); actual=checkpoint()
    if a.mode=='write':
        a.path.write_text(json.dumps(actual,sort_keys=True),encoding='utf-8')
        print('Saved four-table evidence/provider checkpoint (hashes only).')
    else:
        if actual!=json.loads(a.path.read_text(encoding='utf-8')): raise RuntimeError('provider data changed across restart')
        print('PASS: four evidence/provider tables unchanged after real PostgreSQL restart; provider responses are synthetic.')


if __name__=='__main__': main()
