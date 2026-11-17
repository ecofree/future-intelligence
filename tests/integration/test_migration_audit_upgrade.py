"""Disposable DB: verify the accessible remote 13->14 upgrade, not Grok's unseen 0015."""
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
                self.assertEqual(migration.migrate(dsn),['0014_explicit_forecast_contract.sql'])
                with psycopg.connect(dsn) as c:
                    self.assertEqual(c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0],before)
                    after=dict(c.execute('SELECT version,sha256 FROM fi_meta.schema_migrations').fetchall())
                self.assertTrue(all(after[n]==h for n,h in hashes.items()))
                self.assertEqual(migration.migrate(dsn),[])
                spec=importlib.util.spec_from_file_location('fi_upgrade_audit',Path(__file__).resolve().parents[2]/'scripts/audit_migrations.py')
                tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
                self.assertEqual(tool.audit(dsn)['status'],'MATCH')
        finally:
            with psycopg.connect(previous.DSN,autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
