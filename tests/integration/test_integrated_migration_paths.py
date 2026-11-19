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

    def constraints(self,dsn):
        with psycopg.connect(dsn) as c:
            return c.execute("SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='fi' "
                "ORDER BY t.relname,c.conname").fetchall()

    def views(self,dsn):
        with psycopg.connect(dsn) as c:
            return c.execute("SELECT c.relname,pg_get_viewdef(c.oid),c.relacl::text,c.reloptions "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='fi' AND c.relkind='v' ORDER BY c.relname").fetchall()

    def check_path(self,kind):
        migration=importlib.import_module('future_intelligence.db.migrate')
        all_files={p.name:p.read_bytes() for p in files('future_intelligence.db').joinpath('migrations').iterdir()
                   if p.name.endswith('.sql')}
        self.assertEqual(len(all_files),21)
        with self.database() as reference,self.database() as upgraded,tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(migration.migrate(reference),sorted(all_files))
            selected=[n for n in all_files if n[:4]<'0014']
            if kind=='compatible14':selected+=['0014_openai_compatible_provider.sql']
            elif kind=='explicit14':selected+=['0014_explicit_forecast_contract.sql']
            elif kind=='integrated16':selected=[n for n in all_files if n[:4]<'0016']
            elif kind=='canonical17':selected=[n for n in all_files if n[:4]<'0017']
            elif kind=='exact18':selected=[n for n in all_files if n[:4]<'0018']
            elif kind=='canonical19':selected=[n for n in all_files if n[:4]<'0019']
            elif kind=='observed20':selected=[n for n in all_files if n[:4]<'0020']
            root=Path(tmp);target=root/'migrations';target.mkdir()
            for name in selected:(target/name).write_bytes(all_files[name])
            with patch.object(migration,'files',return_value=root):
                self.assertEqual(migration.migrate(upgraded),sorted(selected))
            from future_intelligence.evidence.repository import EvidenceRepository
            self.assertEqual(EvidenceRepository(upgraded).supports_observation_v2(),kind=='observed20')
            with psycopg.connect(upgraded) as c:
                sid=c.execute('SELECT fi.register_source(%s,%s)',
                    ('SYNTHETIC PATH SENTINEL','https://example.invalid/sentinel')).fetchone()[0]
                row=c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0]
                history=c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations ORDER BY version').fetchall()
            self.assertEqual(migration.migrate(upgraded),sorted(set(all_files)-set(selected)))
            self.assertTrue(EvidenceRepository(upgraded).supports_observation_v2())
            with psycopg.connect(upgraded) as c:
                self.assertEqual(c.execute('SELECT to_jsonb(s) FROM fi.sources s WHERE id=%s',(sid,)).fetchone()[0],row)
                after={r[0]:r for r in c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations').fetchall()}
            self.assertTrue(all(after[r[0]]==r for r in history))
            self.assertEqual({n:r[1] for n,r in after.items()},
                             {n:hashlib.sha256(raw).hexdigest() for n,raw in all_files.items()})
            self.assertEqual(self.definitions(upgraded),self.definitions(reference))
            self.assertEqual(self.constraints(upgraded),self.constraints(reference))
            self.assertEqual(self.views(upgraded),self.views(reference))
            self.assertEqual(migration.migrate(upgraded),[])

    def test_13_to_current21(self):self.check_path('baseline13')
    def test_compatible14_first_then_explicit_reconcile_generation(self):self.check_path('compatible14')
    def test_explicit14_first_then_compatible_reconcile_generation(self):self.check_path('explicit14')
    def test_integrated16_to_current21(self):self.check_path('integrated16')
    def test_canonical17_to_current21(self):self.check_path('canonical17')
    def test_exact18_to_current21(self):self.check_path('exact18')
    def test_canonical19_to_current21(self):self.check_path('canonical19')
    def test_observed20_to_current21(self):self.check_path('observed20')
