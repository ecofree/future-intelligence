"""Disposable databases only; preserve the original remote 13->14 regression."""
import importlib
import importlib.util
from importlib.resources import files
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import test_delivery_pipeline as previous


class RemoteBaselineUpgradeTests(unittest.TestCase):
    def test_remote_13_to_14_preserves_rows_and_recorded_hashes(self):
        migration=importlib.import_module('future_intelligence.db.migrate')
        name='fi_upgrade_'+uuid4().hex[:16]
        dsn=make_conninfo(previous.DSN,dbname=name)
        with psycopg.connect(previous.DSN,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);target=root/'migrations';target.mkdir()
                for file in files('future_intelligence.db').joinpath('migrations').iterdir():
                    if file.name.endswith('.sql') and file.name[:4]<'0014':
                        (target/file.name).write_bytes(file.read_bytes())
                with patch.object(migration,'files',return_value=root):
                    self.assertEqual(len(migration.migrate(dsn)),13)
                with psycopg.connect(dsn) as c:
                    sid=c.execute('SELECT fi.register_source(%s,%s)',('SYNTHETIC UPGRADE ROW','https://example.invalid/upgrade')).fetchone()[0]
                    before=c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0]
                    hashes=dict(c.execute('SELECT version,sha256 FROM fi_meta.schema_migrations').fetchall())
                # Keep this original test specifically about PR10's remote 13->14.
                # The combined 16/17-file paths are tested independently below.
                explicit='0014_explicit_forecast_contract.sql'
                (target/explicit).write_bytes(files('future_intelligence.db').joinpath('migrations',explicit).read_bytes())
                with patch.object(migration,'files',return_value=root):
                    self.assertEqual(migration.migrate(dsn),[explicit])
                    self.assertEqual(migration.migrate(dsn),[])
                with psycopg.connect(dsn) as c:
                    self.assertEqual(c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0],before)
                    after=dict(c.execute('SELECT version,sha256 FROM fi_meta.schema_migrations').fetchall())
                self.assertTrue(all(after[n]==h for n,h in hashes.items()))
                spec=importlib.util.spec_from_file_location('fi_upgrade_audit',Path(__file__).resolve().parents[2]/'scripts/audit_migrations.py')
                tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
                self.assertEqual(tool.audit(dsn,target)['status'],'MATCH')
        finally:
            with psycopg.connect(previous.DSN,autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


class HistoricalOrderAuditTests(unittest.TestCase):
    """Synthetic inventories in disposable databases, not the user's live history."""
    def inspect_case(self, names, tied=False):
        from datetime import datetime, timedelta, timezone
        import hashlib
        name = 'fi_history_' + uuid4().hex[:16]
        dsn = make_conninfo(previous.DSN, dbname=name)
        with psycopg.connect(previous.DSN, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with tempfile.TemporaryDirectory() as directory:
                rows = []
                start = datetime(2026, 1, 1, tzinfo=timezone.utc)
                for index, version in enumerate(names):
                    raw = ('-- SYNTHETIC INVENTORY ONLY ' + version + '\n').encode()
                    (Path(directory)/version).write_bytes(raw)
                    rows.append((version, hashlib.sha256(raw).hexdigest(),
                                 start + timedelta(seconds=0 if tied else index)))
                with psycopg.connect(dsn) as c:
                    c.execute('CREATE SCHEMA fi_meta')
                    c.execute('CREATE TABLE fi_meta.schema_migrations '
                              '(version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL)')
                    c.execute('CREATE TABLE preserved_records (id integer PRIMARY KEY, note text NOT NULL)')
                    c.execute("INSERT INTO preserved_records VALUES (1, 'SYNTHETIC RECORD - must not change')")
                    for row in rows:
                        c.execute('INSERT INTO fi_meta.schema_migrations VALUES (%s,%s,%s)', row)
                    before = c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations ORDER BY version').fetchall()
                    data_before = c.execute('SELECT * FROM preserved_records ORDER BY id').fetchall()
                spec = importlib.util.spec_from_file_location('fi_history_order_audit',
                        Path(__file__).resolve().parents[2]/'scripts/audit_migrations.py')
                tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
                report = tool.audit(dsn, directory)
                with psycopg.connect(dsn) as c:
                    self.assertEqual(c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations ORDER BY version').fetchall(), before)
                    self.assertEqual(c.execute('SELECT * FROM preserved_records ORDER BY id').fetchall(), data_before)
                self.assertEqual(report['checksum_mismatches'], [])
                self.assertFalse(report['repair_performed'])
                self.assertFalse(report['production_acceptance'])
                return report
        finally:
            # Only the uniquely named database created by this test is removed.
            with psycopg.connect(previous.DSN, autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))

    def test_equal_hashes_cannot_hide_reversed_order_or_assumed_reconciliation(self):
        report = self.inspect_case(['0014_b.sql', '0014_a.sql', '0015_reconcile.sql'])
        self.assertEqual(report['status'], 'REVIEW_REQUIRED_ORDER')
        self.assertEqual(report['historical_order_status'], 'NONCANONICAL_RECORDED_ORDER')
        self.assertEqual(len(report['applied_order_inversions']), 1)

    def test_tied_timestamps_require_review_without_alphabetic_assumption(self):
        report = self.inspect_case(['0014_a.sql', '0014_b.sql', '0015_reconcile.sql'], tied=True)
        self.assertEqual(report['status'], 'REVIEW_REQUIRED_ORDER')
        self.assertEqual(report['historical_order_status'], 'INDETERMINATE_TIED_TIMESTAMPS')
        self.assertEqual(report['applied_order_inversions'], [])

    def test_two_matching_0014_files_in_recorded_order_remain_allowed(self):
        report = self.inspect_case(['0014_a.sql', '0014_b.sql', '0015_reconcile.sql'])
        self.assertEqual(report['status'], 'MATCH')
        self.assertEqual(report['historical_order_status'], 'CONSISTENT_RECORDED_TIMESTAMPS')
        self.assertEqual(report['duplicate_numeric_prefixes'], ['0014'])
