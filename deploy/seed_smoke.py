"""CI ONLY. Synthetic fixture data, never a live forecast or external service call."""
from datetime import datetime,timedelta,timezone
import json
import os
from uuid import uuid4
import psycopg
from psycopg.conninfo import make_conninfo
from future_intelligence.runtime.secrets import read_secret
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.tasks.repository import TaskRepository
from future_intelligence.tasks.fixture_worker import run_once

if os.environ.get('FI_SYNTHETIC_DEPLOYMENT_TEST')!='1':
    raise SystemExit('Explicit disposable deployment-test authorization required')
root=read_secret('FI_MIGRATION_DATABASE_URL')
accounts=json.loads(read_secret('FI_BOOTSTRAP_CONFIG'))
def dsn(role):
    item=accounts[role]
    return make_conninfo(root,user=item['login'],password=item['password'])
now=datetime.now(timezone.utc)
with psycopg.connect(dsn('source_admin')) as c:
    sid=c.execute('SELECT fi.register_source(%s,%s)',('SYNTHETIC DEPLOYMENT TEST','https://example.invalid/test')).fetchone()[0]
    pid=c.execute('SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',(sid,
        ['internal_research','model_processing','model_evaluation','storage'],now-timedelta(days=1),
        now+timedelta(days=1),'TEST ONLY synthetic permission')).fetchone()[0]
spec=dict(title='SYNTHETIC DEPLOYMENT TEST',event_definition='Test record, no real-world prediction',
    domain='MACRO',track='FAST',question_type='binary',event_cluster='TEST-DEPLOYMENT',
    original_timezone='UTC',forecast_deadline=(now+timedelta(minutes=10)).isoformat(),
    outcome_not_before=(now+timedelta(minutes=20)).isoformat(),resolution_deadline=(now+timedelta(minutes=30)).isoformat(),
    resolution_rule='TEST outcome only, no live result',data_revision_policy='FIRST_RELEASE',unit='boolean',bin_edges=[])
author=QuestionRepository(dsn('author'));reviewer=QuestionRepository(dsn('admission_reviewer'))
v=author.create_version('deployment-'+uuid4().hex,spec,[str(pid)])
reviewer.review(v,'Independent synthetic test question review');author.freeze(v)
operator=TaskRepository(dsn('operator'))
exp=operator.register('deploy-exp-'+uuid4().hex,[v],[dict(entry_key='model-0',provider='fixture',model_id='synthetic',prompt_sha256='a'*64,parameters={})],
    dict(protocol='fi.experiment.v1',suite='A',run_kind='FIXTURE',forecast_deadline=spec['forecast_deadline'],
         budget_microusd=1000,attempt_cap_microusd=100,max_attempts=2,lease_seconds=60,
         failure_policy='REPORT_MISSING',baseline_policy='UNIFORM_CATEGORIES_V1',disclosure_policy='COMPLETE_BATCH_AFTER_RESOLUTION'))
operator.bind_worker(exp,'model-0',accounts['worker_openai']['login'])
assert run_once(TaskRepository(dsn('worker_openai')),exp)['run_kind']=='FIXTURE'
print('PASS: one synthetic experiment persisted with one fixture submission; no external requests.')
