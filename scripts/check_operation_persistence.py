"""CI-only restart check: operation ledger fingerprints and role-enforced pause."""
import hashlib
import json
import os
from pathlib import Path
import sys
import psycopg
from psycopg import sql
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'/'integration'))
from test_operational_admission import OperationalAdmissionTests

TABLES=('operation_policies','admission_checks','admission_confirmations','operation_events')
DSN=os.environ['FI_TEST_DATABASE_URL']
def fingerprints():
    result={}
    with psycopg.connect(DSN) as c:
        for table in TABLES:
            rows=c.execute(sql.SQL('SELECT row_to_json(t)::text FROM fi.{} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table))).fetchall()
            result[table]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(r[0] for r in rows).encode()).hexdigest()}
    return result
mode,path=sys.argv[1],Path(sys.argv[2])
if mode=='write':
    OperationalAdmissionTests.setUpClass()
    case=OperationalAdmissionTests()
    e,v,p,bid=case.anchored()
    case.call('operator','SELECT fi.set_operation_pause(%s,true,%s)',(e,'Synthetic restart pause checkpoint'))
    result={'fingerprints':fingerprints(),'experiment':e,'operator_dsn':case.dsns['operator'],'worker_dsn':case.dsns['worker']}
    path.write_text(json.dumps(result));path.chmod(0o600)
    print('PASS: synthetic operation snapshots and pause checkpoint written; contains private test DSNs, never upload.')
elif mode=='verify':
    saved=json.loads(path.read_text())
    if fingerprints()!=saved['fingerprints']:raise SystemExit('Operation ledger changed across actual restart')
    from future_intelligence.delivery.repository import DeliveryRepository
    from future_intelligence.tasks.repository import TaskRepository
    status=DeliveryRepository(saved['operator_dsn']).one('SELECT fi.operation_status(%s::uuid)',(saved['experiment'],))
    if status['reason']!='OPERATOR_PAUSED':raise SystemExit('Lost paused state')
    try:TaskRepository(saved['worker_dsn']).claim(saved['experiment'],'LIVE')
    except psycopg.errors.ObjectNotInPrerequisiteState:pass
    else:raise SystemExit('Paused task was dispatched after restart')
    print('PASS: four operation tables and pause enforcement persist across actual PostgreSQL restart.')
else:raise SystemExit('write or verify required')
