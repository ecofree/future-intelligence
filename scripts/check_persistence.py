"""CI-only checkpoint across a real server restart; does not reset any database."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql

TABLES = ('sources', 'source_permissions', 'permission_revocations', 'questions',
          'question_versions', 'question_sources', 'question_states', 'audit_events')


def checkpoint():
    dsn = os.environ.get('FI_TEST_DATABASE_URL')
    if not dsn:
        raise SystemExit('FI_TEST_DATABASE_URL required')
    result = {}
    with psycopg.connect(dsn) as connection:
        connection.execute("SET TIME ZONE 'UTC'")
        for table in TABLES:
            rows = connection.execute(sql.SQL(
                'SELECT row_to_json(t)::text FROM fi.{} t ORDER BY row_to_json(t)::text'
            ).format(sql.Identifier(table))).fetchall()
            result[table] = {
                'rows': len(rows),
                'sha256': hashlib.sha256(json.dumps([x[0] for x in rows], ensure_ascii=False).encode()).hexdigest(),
            }
        if result['question_versions']['rows'] == 0:
            raise RuntimeError('empty database is not a persistence test')
        result['frozen'] = connection.execute("SELECT count(*) FROM fi.question_states WHERE status='FROZEN'").fetchone()[0]
        if not result['frozen']:
            raise RuntimeError('no frozen records tested')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['write', 'verify'])
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    actual = checkpoint()
    if args.mode == 'write':
        args.path.write_text(json.dumps(actual, sort_keys=True), encoding='utf-8')
        print('Persistence checkpoint saved; contains row hashes, no database credentials.')
    else:
        expected = json.loads(args.path.read_text(encoding='utf-8'))
        if actual != expected:
            raise RuntimeError('database state changed across restart')
        print('PASS: all eight tables and frozen records survived actual PostgreSQL restart.')


if __name__ == '__main__':
    main()
