"""Real cumulative PostgreSQL lists. All created records are synthetic fixtures."""
import importlib.util
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient
import test_delivery_pipeline as previous
from future_intelligence.delivery.workbench import create_app


class CumulativeListTests(previous.DeliveryPipelineTests):
    def collect(self,resource,page_size=37,experiment=None,role='dashboard'):
        rows=[];cursor=None
        for _ in range(10000):
            page=self.d[role].list_page(resource,page_size,cursor,experiment)
            self.assertLessEqual(len(page['items']),page_size)
            rows.extend(page['items']);cursor=page['next_cursor']
            if not cursor:return rows
        self.fail('pagination failed to terminate')

    def test_205_experiments_survive_first_page_limit_and_repeated_listing(self):
        x=self.setup_experiment(models=1)
        spec=self.d['operator'].one('SELECT spec FROM fi.experiments WHERE id=%s',(x['experiment'],))
        entry=dict(entry_key='model-0',provider='fixture',model_id='pagination-fixture',prompt_sha256='a'*64,parameters={})
        expected={str(x['experiment'])}
        for _ in range(204):
            expected.add(str(self.t['operator'].register('page-fixture-'+uuid4().hex,[x['version']],[entry],spec)))
        first=self.d['operator'].overview();self.assertEqual(len(first),200)
        missing=expected-{str(r['id']) for r in first};self.assertTrue(missing)
        target=sorted(missing)[0]
        exact=self.d['operator'].overview(experiment_id=target)
        self.assertEqual([str(r['id']) for r in exact],[target]);self.assertEqual(exact[0]['run_kind'],'FIXTURE')
        before=self.admin('SELECT count(*) FROM fi.experiments')
        rows=self.collect('experiments');ids=[str(r['id']) for r in rows]
        self.assertEqual(len(ids),before);self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(expected<=set(ids));self.assertEqual(ids,sorted(ids))
        # A second traversal against the same accumulating DB must still work.
        self.assertEqual(ids,[str(r['id']) for r in self.collect('experiments',200)])
        self.assertEqual(self.admin('SELECT count(*) FROM fi.experiments'),before)

    def test_all_four_real_views_and_exact_scopes(self):
        x=self.setup_experiment(models=1);reg=self.registration(x);self.complete(x);final=self.final(x)
        run=str(self.d['scorer'].score(x['experiment']))
        batches=self.collect('batches',1,str(x['experiment']))
        self.assertEqual({str(r['id']) for r in batches},{reg['batch_id'],final['batch_id']})
        runs=self.collect('runs',1,str(x['experiment']))
        self.assertEqual([str(r['id']) for r in runs],[run])
        outcomes=self.collect('outcomes',17,role='resolver')
        ids=[str(r['version_id']) for r in outcomes]
        self.assertIn(str(x['version']),ids);self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(ids,sorted(ids))
        self.assertEqual(self.d['dashboard'].list_page('experiments',1,experiment_id=str(uuid4()))['items'],[])

    def test_cursor_does_not_confer_database_privilege(self):
        for resource in ('experiments','batches','runs'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.d['resolver'].list_page(resource,1)

    def test_http_real_pages_and_filter_preserve_roles(self):
        x=self.setup_experiment(models=1)
        accounts={r:(c*40,self.d[d]) for r,c,d in [('dashboard','d','dashboard'),('resolver','r','resolver')]}
        client=TestClient(create_app(accounts));h={'Authorization':'Bearer '+'d'*40}
        response=client.get('/api/v1/experiments',params={'page_size':1,'experiment_id':str(x['experiment'])},headers=h)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()[0]['id'],str(x['experiment']))
        self.assertEqual(response.headers['x-has-more'],'false')
        self.assertEqual(client.get('/api/v1/batches?page_size=1',headers={'Authorization':'Bearer '+'r'*40}).status_code,403)

    def test_read_only_migration_audit_leaves_metadata_identical(self):
        path=Path(__file__).resolve().parents[2]/'scripts/audit_migrations.py'
        spec=importlib.util.spec_from_file_location('fi_readonly_audit',path)
        tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
        statement="SELECT jsonb_agg(to_jsonb(m) ORDER BY version) FROM fi_meta.schema_migrations m"
        before=self.admin(statement);report=tool.audit(previous.DSN);after=self.admin(statement)
        self.assertEqual(report['status'],'MATCH');self.assertEqual(before,after)
        self.assertTrue(report['read_only']);self.assertFalse(report['repair_performed'])


for _name in tuple(previous.DeliveryPipelineTests.__dict__):
    if _name.startswith('test_'):setattr(CumulativeListTests,_name,None)
