"""CI-only: snapshot task ledger, restart PostgreSQL externally, verify and resume.

All generated examples are FIXTURE. The checkpoint stores identifiers and hashes,
not passwords. Run only against a disposable FI_TEST_DATABASE_URL database.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'tests'))
from test_question_spec import example_spec
from test_experiment_spec import experiment_spec, entries

TABLES = ('experiments','experiment_entries','worker_bindings','expected_tasks',
          'task_states','task_attempts','budget_accounts','budget_reservations',
          'task_submissions','outbox_events','outbox_delivery')
DSN = os.environ['FI_TEST_DATABASE_URL']


def call(login,statement,params=()):
    with psycopg.connect(DSN) as c:
        if login:
            c.execute(sql.SQL('SET SESSION AUTHORIZATION {}').format(sql.Identifier(login)))
        return c.execute(statement,params).fetchone()[0]


def fingerprints():
    result={}
    with psycopg.connect(DSN) as c:
        for table in TABLES:
            rows=c.execute(sql.SQL('SELECT to_jsonb(t)::text FROM fi.{} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table))).fetchall()
            digest=hashlib.sha256('\n'.join(row[0] for row in rows).encode()).hexdigest()
            result[table]={'rows':len(rows),'sha256':digest}
    return result


def write(path):
    suffix=uuid4().hex[:10]; logins={}
    with psycopg.connect(DSN) as c:
        for role in ('author','reviewer','source_admin','operator','worker'):
            login=f'fi_restart_{role}_{suffix}';logins[role]=login
            c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT').format(sql.Identifier(login)))
            c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier('fi_'+role),sql.Identifier(login)))
    now=datetime.now(timezone.utc)
    sid=call(logins['source_admin'],'SELECT fi.register_source(%s,%s)',('Restart FIXTURE','https://example.invalid/restart'))
    pid=call(logins['source_admin'],'SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',
        (sid,['internal_research','model_processing','model_evaluation','storage'],now-timedelta(days=1),now+timedelta(days=2),'TEST FIXTURE ONLY'))
    versions=[]
    for i in range(3):
        vid=call(logins['author'],'SELECT fi.create_version(%s,%s,%s::uuid[],NULL)',
                 (f'restart-{suffix}-{i}',Jsonb(example_spec()),[pid]))
        call(logins['reviewer'],'SELECT fi.review_version(%s,%s)',(vid,'fixture review'))
        call(logins['author'],'SELECT fi.freeze_version(%s)',(vid,)); versions.append(vid)
    spec=experiment_spec(lease_seconds=2)
    exp=call(logins['operator'],'SELECT fi.register_experiment(%s,%s::uuid[],%s,%s)',
             (f'restart-exp-{suffix}',versions,Jsonb(entries()),Jsonb(spec)))
    call(logins['operator'],'SELECT fi.bind_worker(%s,%s,%s::name)',(exp,'model-0',logins['worker']))
    before=call(logins['worker'],'SELECT fi.claim_task(%s,%s)',(exp,'FIXTURE'))
    sent=call(logins['worker'],'SELECT fi.claim_task(%s,%s)',(exp,'FIXTURE'))
    call(logins['worker'],'SELECT fi.start_task_call(%s::uuid,%s::uuid,%s)',
         (sent['task_id'],sent['attempt_id'],sent['fence']))
    checkpoint={'experiment_id':str(exp),'logins':logins,'before_call':before,'sent_call':sent,'tables':fingerprints()}
    path.write_text(json.dumps(checkpoint,sort_keys=True),encoding='utf-8')
    print('Saved task-ledger FIXTURE hashes; no passwords or real provider requests.')


def verify(path):
    check=json.loads(path.read_text(encoding='utf-8'))
    assert fingerprints()==check['tables'], 'Task-ledger content changed across server restart'
    time.sleep(2.1)
    op=check['logins']['operator']; worker=check['logins']['worker']; exp=check['experiment_id']
    assert call(op,'SELECT fi.recover_tasks(%s::uuid,100)',(exp,))==2
    old=check['before_call']; sent=check['sent_call']
    assert call(None,'SELECT status FROM fi.task_states WHERE task_id=%s::uuid',(old['task_id'],))=='QUEUED'
    assert call(None,'SELECT status FROM fi.task_states WHERE task_id=%s::uuid',(sent['task_id'],))=='UNCERTAIN'
    assert call(None,'SELECT reserved_microusd FROM fi.budget_accounts WHERE experiment_id=%s::uuid',(exp,))==100
    try:
        call(worker,'SELECT fi.start_task_call(%s::uuid,%s::uuid,%s)',(old['task_id'],old['attempt_id'],old['fence']))
    except psycopg.errors.ObjectNotInPrerequisiteState:
        pass
    else:
        raise AssertionError('Expired worker unexpectedly regained authority')
    # Both undispatched tasks may be claimed; the unknown dispatched task must not.
    claims=[call(worker,'SELECT fi.claim_task(%s::uuid,%s)',(exp,'FIXTURE')) for _ in range(2)]
    assert all(lease and lease['task_id']!=sent['task_id'] for lease in claims)
    resumed=next(lease for lease in claims if lease['task_id']==old['task_id'])
    assert resumed['fence']>old['fence']
    print('PASS: 11 ledger tables survived actual server restart; safe work resumed, old fence rejected, unknown charge retained.')


if __name__=='__main__':
    action,path=sys.argv[1:]
    {'write':write,'verify':verify}[action](Path(path))
