"""One-shot migrator and role provisioning. Never mounted in app/worker services."""
import json
import os
from pathlib import Path
import sys
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from future_intelligence.db.migrate import migrate
from future_intelligence.runtime.secrets import read_secret

EXPECTED={'author':'author','admission_reviewer':'reviewer','source_admin':'source_admin',
          'operator':'operator','verifier':'verifier','resolver':'resolver','reviewer':'resolution_reviewer',
          'scorer':'scorekeeper','dashboard':'dashboard','worker_openai':'worker','worker_anthropic':'worker','worker_google':'worker'}


def main():
    dsn=read_secret('FI_MIGRATION_DATABASE_URL')
    accounts=json.loads(read_secret('FI_BOOTSTRAP_CONFIG'))
    if set(accounts)!=set(EXPECTED):raise ValueError('unexpected role manifest')
    applied=migrate(dsn)
    with psycopg.connect(dsn) as c:
        for name,suffix in EXPECTED.items():
            item=accounts[name];login='fi_local_'+name;role='fi_'+suffix
            if set(item)!={'login','role','password'} or item['login']!=login or item['role']!=role or len(item['password'])<32:
                raise ValueError('invalid role contract')
            existing=c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname=%s',(login,)).fetchone()
            if existing:
                if existing!=(False,False,False,False,True):raise ValueError('unsafe pre-existing login')
                memberships={r[0] for r in c.execute('SELECT g.rolname FROM pg_auth_members m JOIN pg_roles u ON u.oid=m.member JOIN pg_roles g ON g.oid=m.roleid WHERE u.rolname=%s',(login,))}
                if memberships!={role}:raise ValueError('pre-existing login membership differs')
            else:
                c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}').format(sql.Identifier(login),sql.Literal(item['password'])))
                c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(role),sql.Identifier(login)))
    # Verify credentials, not just role metadata. Never reset an existing password.
    for item in accounts.values():
        with psycopg.connect(make_conninfo(dsn,user=item['login'],password=item['password'])) as c:
            if c.execute('SELECT session_user').fetchone()[0]!=item['login']:raise ValueError('identity mismatch')
    print(json.dumps({'status':'MIGRATED_AND_IDENTITIES_VERIFIED','applied':applied,'roles':len(accounts),'production_acceptance':'NOT_IMPLIED'}))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'SETUP_BLOCKED','error_type':type(exc).__name__}));sys.exit(2)
