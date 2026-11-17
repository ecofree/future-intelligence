"""Minimal local internal workbench; separate DB identities per API role.

No public deployment, password login, CORS bypass or model-call endpoint.
"""
from __future__ import annotations

import hmac
import asyncio
import json
import os
from uuid import UUID

HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Future Intelligence · 内部研究工作台</title><link rel="stylesheet" href="/ui.css">
<header><small>FUTURE INTELLIGENCE / V2.0.1</small><h1>预测与验证工作台</h1><p>候选输出、时间资格与现实结果分别展示。测试数据不代表真实预测成绩。</p></header>
<main><section><label>访问令牌 <input id="token" type="password" autocomplete="off" placeholder="仅保存在当前页面内存"></label>
<button id="load">连接</button><span id="status">未连接</span></section>
<nav><button data-view="experiments">实验与预算</button><button data-view="batches">批次与时间证明</button><button data-view="outcomes">独立结果</button><button data-view="runs">评分档案</button></nav>
<section><h2 id="heading">请选择页面</h2><p id="notice">不将缺失预测删除，不将无时间证明的记录当正式成绩。</p><div id="rows"></div></section>
<section><h2>运行边界</h2><p>默认只监听本机。模型与 TSA 凭证由运行环境注入。真实验收状态以仓库报告和可复核记录为准。</p></section></main><script src="/ui.js"></script></html>'''
CSS = '''body{margin:0;background:#f3f5f7;color:#192b3c;font:15px/1.6 system-ui,sans-serif}header,main{max-width:1150px;margin:auto;padding:24px}header{padding-top:44px}h1{font-size:32px;margin:8px 0}small{letter-spacing:2px;color:#476b78}section{background:white;border:1px solid #dbe3e8;border-radius:10px;padding:20px;margin:16px 0}button,input{padding:10px;border-radius:6px;border:1px solid #bacad4;margin-right:8px}button{cursor:pointer;background:#e6f0f3;color:#123c4b}nav{display:flex;gap:8px;flex-wrap:wrap}input{min-width:290px}#status{color:#526571}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:10px;border-bottom:1px solid #e3e8ee;font-size:13px}#rows{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}.pill{font-size:12px;color:#586d79}'''
JS = '''let view='experiments';const el=id=>document.getElementById(id);const names={experiments:'实验与预算',batches:'批次与时间证明',outcomes:'独立结果',runs:'评分档案'};
async function load(){el('heading').textContent=names[view];el('status').textContent='读取中';el('rows').replaceChildren();try{const r=await fetch('/api/v1/'+view,{headers:{Authorization:'Bearer '+el('token').value},cache:'no-store'});if(!r.ok)throw new Error('访问失败：'+r.status);const data=await r.json();el('status').textContent='已连接 · '+data.length+' 条';if(!data.length){el('rows').textContent='暂无记录';return;}const t=document.createElement('table'),head=document.createElement('tr');const keys=Object.keys(data[0]).filter(k=>!['spec','analysis_plan','trust_profile'].includes(k));keys.forEach(k=>{const th=document.createElement('th');th.textContent=k;head.append(th)});t.append(head);data.forEach(row=>{const tr=document.createElement('tr');keys.forEach(k=>{const td=document.createElement('td');td.textContent=row[k]===null?'—':typeof row[k]==='object'?JSON.stringify(row[k]):String(row[k]);tr.append(td)});if(view==='runs'){tr.style.cursor='pointer';tr.onclick=async()=>{const r=await fetch('/api/v1/runs/'+row.id,{headers:{Authorization:'Bearer '+el('token').value},cache:'no-store'});if(r.ok){const p=document.createElement('pre');p.textContent=JSON.stringify(await r.json(),null,2);el('rows').replaceChildren(p)}}}t.append(tr)});el('rows').append(t);}catch(e){el('status').textContent=e.message}}
el('load').onclick=load;document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;load()});'''


def create_app(accounts: dict):
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import HTMLResponse, Response, JSONResponse
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi import Depends
    from pydantic import BaseModel, ConfigDict
    # accounts = role -> (high-entropy token, role-specific repository).
    allowed={'dashboard','resolver','reviewer','scorer'}
    if not accounts or set(accounts)-allowed:
        raise ValueError('explicit internal accounts required')
    tokens=[pair[0] for pair in accounts.values()]
    if any(not isinstance(t,str) or len(t)<32 for t in tokens) or len(set(tokens))!=len(tokens):
        raise ValueError('unique high-entropy tokens required')
    app=FastAPI(title='Future Intelligence Internal',docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.middleware('http')
    async def headers(request, call_next):
        if request.method not in ('GET','POST'):
            return JSONResponse({'error':'METHOD_NOT_ALLOWED'},status_code=405)
        if request.method=='POST':
            try:
                length=int(request.headers.get('content-length','-1'))
            except ValueError:
                length=-1
            if length<0 or length>16384:
                return JSONResponse({'error':'BODY_LENGTH_REQUIRED_OR_TOO_LARGE'},status_code=413)
            body=bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body)>16384:
                            return JSONResponse({'error':'BODY_TOO_LARGE'},status_code=413)
            except (TimeoutError,RuntimeError):
                return JSONResponse({'error':'INCOMPLETE_BODY'},status_code=408)
            if len(body)!=length:
                return JSONResponse({'error':'BODY_LENGTH_MISMATCH'},status_code=400)
            request._body=bytes(body)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def require(*roles):
        def auth(authorization: str=Header(default='')):
            if not authorization.startswith('Bearer '):
                raise HTTPException(401,'AUTH_REQUIRED')
            supplied=authorization[7:]
            for role,(token,repository) in accounts.items():
                if hmac.compare_digest(token.encode(),supplied.encode()):
                    if role not in roles:
                        raise HTTPException(403,'ROLE_FORBIDDEN')
                    return repository
            raise HTTPException(401,'INVALID_TOKEN')
        return auth

    def safe(fn,*args):
        try:
            return fn(*args)
        except (ValueError,KeyError):
            raise HTTPException(422,'INVALID_REQUEST') from None
        except Exception:
            # Do not return database detail, credentials or provider response bodies.
            raise HTTPException(409,'STATE_OR_PERMISSION_CONFLICT') from None

    @app.get('/',response_class=HTMLResponse)
    def index(): return HTML
    @app.get('/ui.js')
    def js(): return Response(JS,media_type='text/javascript')
    @app.get('/ui.css')
    def css(): return Response(CSS,media_type='text/css')
    @app.get('/healthz')
    def health(): return {'service':'future-intelligence','mode':'INTERNAL_ONLY','production_acceptance':'NOT_IMPLIED'}
    @app.get('/api/v1/experiments')
    def experiments(repo=Depends(require('dashboard','scorer'))): return safe(repo.overview)
    @app.get('/api/v1/batches')
    def batches(repo=Depends(require('dashboard','scorer'))): return safe(repo.batches)
    @app.get('/api/v1/outcomes')
    def outcomes(repo=Depends(require('dashboard','resolver','reviewer','scorer'))): return safe(repo.outcomes)
    @app.get('/api/v1/runs')
    def runs(repo=Depends(require('dashboard','scorer'))): return safe(repo.score_runs)
    @app.get('/api/v1/runs/{run_id}')
    def scores(run_id: UUID,repo=Depends(require('dashboard','scorer'))): return safe(repo.score_summary,str(run_id))

    # JSON bodies are strictly bounded and checked rather than accepting arbitrary SQL.
    @app.post('/api/v1/resolutions')
    def propose(body: dict,repo=Depends(require('resolver'))):
        required={'version','kind','evidence_id','reason','parent','fallback_reason'}
        if set(body)!=required: raise HTTPException(422,'EXACT_RESOLUTION_FIELDS_REQUIRED')
        return {'proposal_id':safe(repo.propose,body['version'],body['kind'],body['evidence_id'],body['reason'],body['parent'],body['fallback_reason'])}
    @app.post('/api/v1/resolutions/{proposal_id}/confirm')
    def confirm(proposal_id: UUID,body: dict,repo=Depends(require('reviewer'))):
        if set(body)!={'note'}:raise HTTPException(422,'EXACT_REVIEW_FIELDS_REQUIRED')
        safe(repo.confirm,str(proposal_id),body['note']);return {'status':'CONFIRMED'}
    @app.post('/api/v1/experiments/{experiment_id}/score')
    def compute(experiment_id: UUID,repo=Depends(require('scorer'))):return {'run_id':safe(repo.score,str(experiment_id))}
    return app


def main():
    from .repository import DeliveryRepository
    import uvicorn
    accounts={}
    for role in ('dashboard','resolver','reviewer','scorer'):
        token=os.getenv('FI_'+role.upper()+'_TOKEN');dsn=os.getenv('FI_'+role.upper()+'_DATABASE_URL')
        if token and dsn:accounts[role]=(token,DeliveryRepository(dsn))
    app=create_app(accounts)
    uvicorn.run(app,host='127.0.0.1',port=8080,access_log=False)


if __name__=='__main__':main()
