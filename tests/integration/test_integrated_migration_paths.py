"""Replay actual committed SQL in disposable DBs; never connects to a live DB."""
import contextlib
import hashlib
import importlib
from importlib.resources import files
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import test_delivery_pipeline as previous


class IntegratedMigrationPathsTests(unittest.TestCase):
    @contextlib.contextmanager
    def database(self):
        name='fi_path_'+uuid4().hex[:16]
        dsn=make_conninfo(previous.DSN,dbname=name)
        with psycopg.connect(previous.DSN,autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:yield dsn
        finally:
            with psycopg.connect(previous.DSN,autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))

    def definitions(self,dsn):
        with psycopg.connect(dsn) as c:
            # Exact installed function definitions, privileges and trigger behavior
            # (not only a filename/checksum inventory).
            return c.execute("SELECT p.proname,pg_get_function_identity_arguments(p.oid),"
                "pg_get_functiondef(p.oid),p.prosecdef,p.proconfig,p.proacl::text "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='fi' ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)").fetchall()

    def check_path(self,kind):
        migration=importlib.import_module('future_intelligence.db.migrate')
        all_files={p.name:p.read_bytes() for p in files('future_intelligence.db').joinpath('migrations').iterdir()
                   if p.name.endswith('.sql')}
        self.assertEqual(len(all_files),17)
        with self.database() as reference,self.database() as upgraded,tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(migration.migrate(reference),sorted(all_files))
            selected=[n for n in all_files if n[:4]<'0014']
            if kind=='compatible14':selected+=['0014_openai_compatible_provider.sql']
            elif kind=='explicit14':selected+=['0014_explicit_forecast_contract.sql']
            elif kind=='integrated16':selected=[n for n in all_files if n[:4]<'0016']
            root=Path(tmp);target=root/'migrations';target.mkdir()
            for name in selected:(target/name).write_bytes(all_files[name])
            with patch.object(migration,'files',return_value=root):
                self.assertEqual(migration.migrate(upgraded),sorted(selected))
            with psycopg.connect(upgraded) as c:
                sid=c.execute('SELECT fi.register_source(%s,%s)',
                    ('SYNTHETIC PATH SENTINEL','https://example.invalid/sentinel')).fetchone()[0]
                row=c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0]
                history=c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations ORDER BY version').fetchall()
            self.assertEqual(migration.migrate(upgraded),sorted(set(all_files)-set(selected)))
            with psycopg.connect(upgraded) as c:
                self.assertEqual(c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0],row)
                after={r[0]:r for r in c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations').fetchall()}
            self.assertTrue(all(after[r[0]]==r for r in history))
            self.assertEqual({n:r[1] for n,r in after.items()},
                             {n:hashlib.sha256(raw).hexdigest() for n,raw in all_files.items()})
            self.assertEqual(self.definitions(upgraded),self.definitions(reference))
            self.assertEqual(migration.migrate(upgraded),[])

    def test_13_to_integrated17(self):self.check_path('baseline13')
    def test_compatible14_first_then_explicit_reconcile_generation(self):self.check_path('compatible14')
    def test_explicit14_first_then_compatible_reconcile_generation(self):self.check_path('explicit14')
    def test_integrated16_to_generation17(self):self.check_path('integrated16')
