"""Internal operator CLI. Credentials are environment-only and never printed."""
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from .repository import DeliveryRepository
from .timestamps import TrustProfile,request_reply
from .batch import strict_json
from .exports import verify_export
from .evaluation import compare_score_rows
from .operations import supervise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capture');c.add_argument('experiment');c.add_argument('kind',choices=['REGISTRATION','FORECAST']);c.add_argument('--plan',type=Path);c.add_argument('--profile',type=Path)
    a=sub.add_parser('anchor');a.add_argument('batch');a.add_argument('--url',required=True);a.add_argument('--allowed-host',required=True);a.add_argument('--allow-network',action='store_true')
    v=sub.add_parser('verify-anchor');v.add_argument('request');v.add_argument('--ca',type=Path,required=True);v.add_argument('--profile',type=Path,required=True)
    e=sub.add_parser('export');e.add_argument('experiment');e.add_argument('output',type=Path)
    v=sub.add_parser('verify-export');v.add_argument('input',type=Path);v.add_argument('--ca',type=Path,required=True);v.add_argument('--profile',type=Path,required=True)
    r=sub.add_parser('resolve');r.add_argument('input',type=Path)
    c=sub.add_parser('confirm');c.add_argument('proposal');c.add_argument('--note',required=True)
    s=sub.add_parser('score');s.add_argument('experiment')
    t=sub.add_parser('analyze');t.add_argument('run');t.add_argument('--diagnostic',action='store_true')
    w=sub.add_parser('worker');w.add_argument('experiment');w.add_argument('--cycles',type=int,default=1);w.add_argument('--interval',type=float,default=10);w.add_argument('--allow-network',action='store_true')
    w.add_argument('--task',type=UUID,help='Claim exactly one task; requires --cycles 1.')
    w.add_argument('--input-count-record',type=Path,help='Validate a matching input-count receipt before sending; requires --task.')
    r=sub.add_parser('recover');r.add_argument('experiment')
    c=sub.add_parser('collect-bls');c.add_argument('permission');c.add_argument('series');c.add_argument('year',type=int);c.add_argument('period');c.add_argument('--allow-network',action='store_true')
    a=sub.add_parser('add-evidence');a.add_argument('input',type=Path)
    args=p.parse_args()
    if args.command=='worker' and args.task is not None and args.cycles!=1:
        raise ValueError('exact task selection requires --cycles 1')
    count_record=None
    if args.command=='worker' and args.input_count_record is not None:
        if args.task is None:raise ValueError('--input-count-record requires --task')
        from future_intelligence.tasks.live_worker import load_count_record
        count_record=load_count_record(args.input_count_record)
    def load(path):
        if path.stat().st_size>50_000_000:raise ValueError('oversized input')
        return strict_json(path.read_text(encoding='utf-8'))
    def profile(path):
        values=load(path);values['policy_oids']=tuple(values['policy_oids']);return TrustProfile(**values)
    if args.command=='verify-export':
        return verify_export(load(args.input),args.ca.read_bytes(),profile(args.profile))
    role={'verify-anchor':'VERIFIER','resolve':'RESOLVER','confirm':'REVIEWER','score':'SCORER','analyze':'SCORER','worker':'WORKER','collect-bls':'SOURCE_ADMIN','add-evidence':'RESOLVER'}.get(args.command,'OPERATOR')
    dsn=os.environ.get('FI_'+role+'_DATABASE_URL')
    if not dsn:raise ValueError('required isolated role DSN missing')
    repo=DeliveryRepository(dsn)
    if args.command=='capture':
        bid=repo.capture(args.experiment,args.kind,load(args.plan) if args.plan else None,load(args.profile) if args.profile else None)
        value=repo.seal(bid);return {'batch_id':bid,'subject_sha256':value['subject_sha256'],'timestamp_status':'NOT_REQUESTED'}
    if args.command=='anchor':
        if not args.allow_network:raise ValueError('explicit network authorization required')
        rid,query=repo.new_anchor(args.batch)
        try:
            reply=request_reply(args.url,query,allowed_host=args.allowed_host)
        except Exception:
            repo.receive(rid,None,'TSA_NETWORK_OR_REPLY_FAILURE');return {'request_id':rid,'status':'FAILED'}
        repo.receive(rid,reply);return {'request_id':rid,'status':'RECEIVED_NOT_VERIFIED'}
    if args.command=='verify-anchor':return repo.verify_anchor(args.request,args.ca.read_bytes(),profile(args.profile))
    if args.command=='export':
        value=repo.export(args.experiment)
        # Never overwrite an older export; no public upload is performed.
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(value,out,ensure_ascii=False,sort_keys=True,allow_nan=False)
        return {'status':'EXPORTED_INTERNAL_ONLY','path':str(args.output)}
    if args.command=='collect-bls':
        if not args.allow_network:raise ValueError('explicit source network authorization required')
        from .operations import fetch_bls
        from psycopg.types.json import Jsonb
        endpoint='https://api.bls.gov/publicAPI/v1/timeseries/data/'+args.series
        permit=repo.one('SELECT fi.admit_bls_collection(%s::uuid,%s)',(args.permission,endpoint))
        summary=fetch_bls(args.series,args.year,args.period,permit=permit)
        capture=repo.one('SELECT fi.store_bls_capture(%s::uuid,%s,%s)',(args.permission,endpoint,Jsonb(summary)))
        return {'capture_id':str(capture),**summary}
    if args.command=='add-evidence':return {'evidence_id':repo.add_evidence(**load(args.input))}
    if args.command=='resolve':
        data=load(args.input)
        return {'proposal_id':repo.propose(**data)}
    if args.command=='confirm':repo.confirm(args.proposal,args.note);return {'status':'CONFIRMED'}
    if args.command=='score':return {'run_id':repo.score(args.experiment)}
    if args.command=='analyze':
        plan=repo.one("SELECT b.analysis_plan FROM fi.score_runs s JOIN fi.commitment_batches b ON b.experiment_id=s.experiment_id AND b.kind='REGISTRATION' WHERE s.id=%s::uuid",(args.run,))
        return compare_score_rows(repo.scores(args.run),plan,diagnostic=args.diagnostic)
    if args.command=='recover':
        from future_intelligence.tasks.repository import TaskRepository
        return {'recovered':TaskRepository(dsn).recover(args.experiment)}
    if args.command=='worker':
        if not args.allow_network:raise ValueError('explicit model network authorization required')
        from future_intelligence.evidence.repository import EvidenceRepository
        from future_intelligence.tasks.live_worker import run_once
        # A dedicated login runs one preregistered entry only. Recovery stays separate.
        worker=EvidenceRepository(dsn)
        def step():
            gate=repo.one('SELECT fi.worker_delivery_gate(%s::uuid)',(args.experiment,))
            if not gate['ready']:return {'status':'BLOCKED','reason':gate['reason']}
            return run_once(worker,args.experiment,task_id=args.task,input_count_record=count_record)
        return supervise(step,cycles=args.cycles,interval=args.interval)


if __name__=='__main__':
    try:
        result=main()
        print(json.dumps(result,ensure_ascii=False,default=str,allow_nan=False))
        # Worker lists preserve their observations; a stopped worker is not success.
        if isinstance(result,list) and any(isinstance(item,dict) and (
            item.get('http_status')==401 or item.get('status') in
            {'BLOCKED','NO_REDISPATCH','STOPPED_UNCERTAIN'}) for item in result):
            sys.exit(2)
    except Exception as exc:
        # No DSNs, keys, HTTP bodies or stack traces in operational output.
        print(json.dumps({'status':'BLOCKED_OR_UNCERTAIN','error_type':type(exc).__name__,'automatic_redispatch':False}))
        sys.exit(2)
