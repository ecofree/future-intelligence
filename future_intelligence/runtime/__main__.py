"""Explicit role commands. Default operations never contact model or TSA services."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import sys
from uuid import UUID
from .secrets import read_secret, inject
from .service import alerts, operator_tick, loop


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('configure');c.add_argument('experiment');c.add_argument('--mode',choices=['FORMAL','DIAGNOSTIC'],required=True)
    c.add_argument('--seal-margin',type=int,default=120);c.add_argument('--check-max-age',type=int,default=3600);c.add_argument('--note',required=True)
    c=sub.add_parser('check');c.add_argument('experiment');c.add_argument('version');c.add_argument('permission');c.add_argument('--kind',choices=['CALENDAR','TARGET_STATUS'],required=True)
    c.add_argument('--finding',choices=['REVIEWED','NOT_FOUND','KNOWN','UNCERTAIN'],required=True);c.add_argument('--reference',required=True);c.add_argument('--document',type=Path,required=True);c.add_argument('--note',required=True)
    c=sub.add_parser('review');c.add_argument('experiment');c.add_argument('version');c.add_argument('--checks',nargs='+',required=True);c.add_argument('--cluster',required=True);c.add_argument('--note',required=True)
    for kind in ('pause','resume'):
        c=sub.add_parser(kind);c.add_argument('experiment');c.add_argument('--note',required=True)
    c=sub.add_parser('status');c.add_argument('experiment')
    for kind in ('operator','worker'):
        c=sub.add_parser(kind);c.add_argument('experiment');c.add_argument('--cycles',type=int,default=1);c.add_argument('--interval',type=float,default=10)
        if kind=='worker':
            c.add_argument('--allow-network',action='store_true')
            c.add_argument('--task',type=UUID,help='Claim exactly one task; requires --cycles 1.')
            c.add_argument('--input-count-record',type=Path,help='Validate a matching input-count receipt before sending; requires --task.')
    c=sub.add_parser('serve');c.add_argument('--container-bind',action='store_true')
    c=sub.add_parser('check-egress', help='Check ordinary DNS and TLS for the four pilot hosts.')
    c.add_argument('--additional-tsa-host', help='Also test TLS to this candidate on port 443.')
    args=p.parse_args(argv)
    if args.command == 'check-egress':
        from future_intelligence.public_https import probe_egress
        report = probe_egress(args.additional_tsa_host)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['tls_passed'] else 2
    if args.command=='worker' and args.task is not None and args.cycles!=1:
        raise ValueError('exact task selection requires --cycles 1')
    count_record=None
    if args.command=='worker' and args.input_count_record is not None:
        if args.task is None:raise ValueError('--input-count-record requires --task')
        from future_intelligence.tasks.live_worker import load_count_record
        count_record=load_count_record(args.input_count_record)
    if args.command=='serve':
        from future_intelligence.delivery.workbench import create_app
        from future_intelligence.delivery.repository import DeliveryRepository
        import uvicorn
        accounts={}
        # A local deployment only exposes a read-only dashboard by default.
        token=read_secret('FI_DASHBOARD_TOKEN')
        dsn=read_secret('FI_DASHBOARD_DATABASE_URL')
        accounts['dashboard']=(token,DeliveryRepository(dsn))
        app=create_app(accounts)
        @app.get('/readyz')
        def ready():
            from fastapi.responses import JSONResponse
            try:
                accounts['dashboard'][1].overview()
                return {'status':'READY_INTERNAL_ONLY','production_acceptance':'NOT_IMPLIED'}
            except Exception:
                return JSONResponse({'status':'DATABASE_NOT_READY'},status_code=503)
        uvicorn.run(app,host='0.0.0.0' if args.container_bind else '127.0.0.1',port=8080,access_log=False)
        return 0
    args.experiment=str(UUID(args.experiment))
    role={'check':'SOURCE_ADMIN','review':'ADMISSION_REVIEWER','worker':'WORKER'}.get(args.command,'OPERATOR')
    dsn=read_secret('FI_'+role+'_DATABASE_URL')
    from future_intelligence.delivery.repository import DeliveryRepository
    repo=DeliveryRepository(dsn)
    if args.command=='configure':
        repo.one('SELECT fi.configure_operation(%s::uuid,%s,%s,%s,%s)',(args.experiment,args.mode,args.seal_margin,args.check_max_age,args.note))
        return {'status':'CONFIGURED','mode':args.mode,'live_acceptance':False}
    if args.command=='check':
        if args.document.stat().st_size>2_000_000:raise ValueError('document too large')
        body=args.document.read_bytes()
        result=repo.one('SELECT fi.add_admission_check(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)',
            (args.experiment,str(UUID(args.version)),str(UUID(args.permission)),args.kind,args.finding,args.reference,body,args.note))
        return {'check_id':str(result),'status':'RECORDED_NOT_INDEPENDENTLY_CONFIRMED'}
    if args.command=='review':
        repo.one('SELECT fi.confirm_admission(%s::uuid,%s::uuid,%s::uuid[],%s,%s)',
            (args.experiment,str(UUID(args.version)),[str(UUID(i)) for i in args.checks],args.cluster,args.note))
        return {'status':'ADMISSION_REVIEWED','external_time_not_implied':True}
    if args.command in ('pause','resume'):
        repo.one('SELECT fi.set_operation_pause(%s::uuid,%s,%s)',(args.experiment,args.command=='pause',args.note))
        return {'status':args.command.upper()}
    if args.command=='status':
        state=repo.one('SELECT fi.operation_status(%s::uuid)',(args.experiment,))
        if not state:raise ValueError('unknown experiment')
        return {**state,'alerts':alerts(state)}
    stopped=[False]
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopped.__setitem__(0,True))
    from future_intelligence.tasks.repository import TaskRepository
    if args.command=='operator':
        tasks=TaskRepository(dsn)
        step=lambda:operator_tick(repo,tasks,args.experiment)
    else:
        if not args.allow_network:raise ValueError('explicit network authorization required')
        from future_intelligence.evidence.repository import EvidenceRepository
        from future_intelligence.tasks.live_worker import run_once
        keys=inject(['OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY'])
        worker=EvidenceRepository(dsn)
        def step():
            gate=repo.one('SELECT fi.worker_delivery_gate(%s::uuid)',(args.experiment,))
            if not gate['ready']:return {'status':'BLOCKED','reason':gate['reason']}
            return run_once(worker,args.experiment,environ=keys,task_id=args.task,input_count_record=count_record)
    return loop(step,cycles=args.cycles,interval=args.interval,emit=lambda obj:print(json.dumps(obj,default=str,allow_nan=False),flush=True),stop=lambda:stopped[0])


if __name__=='__main__':
    try:
        result=main()
        if type(result) is int:sys.exit(result)
        print(json.dumps(result,default=str,allow_nan=False))
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED_OR_UNCERTAIN','error_type':type(exc).__name__,'automatic_redispatch':False}))
        sys.exit(2)
