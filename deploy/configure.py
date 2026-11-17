"""Generate NEW private deployment credentials; never overwrite or print them."""
from pathlib import Path
import argparse
import json
import os
import secrets

ROLES={'author':'author','admission_reviewer':'reviewer','source_admin':'source_admin',
       'operator':'operator','verifier':'verifier','resolver':'resolver',
       'reviewer':'resolution_reviewer','scorer':'scorekeeper','dashboard':'dashboard',
       'worker_openai':'worker','worker_anthropic':'worker','worker_google':'worker'}


def configure(folder: Path):
    folder.mkdir(mode=0o700)  # Deliberately fails if anything already exists.
    os.chmod(folder,0o700)
    def write(name,value):
        fd=os.open(folder/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(value)
    root=secrets.token_hex(32)
    write('db_password',root)
    write('migration_dsn',f'postgresql://postgres:{root}@db:5432/fi')
    accounts={}
    for name,role in ROLES.items():
        password=secrets.token_hex(32);login='fi_local_'+name
        accounts[name]={'login':login,'password':password,'role':'fi_'+role}
        write(name+'_dsn',f'postgresql://{login}:{password}@db:5432/fi')
    write('bootstrap.json',json.dumps(accounts))
    write('dashboard_token',secrets.token_urlsafe(48))
    # No API key is invented. Empty optional files deliberately block workers.
    for name in ('openai','anthropic','google'):write(name+'_key','')
    return {'status':'CREATED_PRIVATE_CONFIGURATION','directory':str(folder),'real_api_verified':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path,nargs='?',default=Path(__file__).parent/'.local-secrets')
    try:print(json.dumps(configure(parser.parse_args().directory)))
    except Exception as exc:raise SystemExit('Configuration not changed safely: '+type(exc).__name__)
