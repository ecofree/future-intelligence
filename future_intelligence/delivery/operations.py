"""Bounded live supervision and an explicit BLS release adapter.

BLS time-series JSON is not an original-release vintage proof. Its output is a
review candidate only; FIRST_RELEASE must be checked against the publication.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

from .batch import strict_json
from .timestamps import utc


class PublicHTTPS(http.client.HTTPSConnection):
    def connect(self):
        records=socket.getaddrinfo(self.host,self.port,type=socket.SOCK_STREAM)
        addresses=sorted({item[4][0] for item in records})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError('nonpublic network address rejected')
        raw=socket.create_connection((addresses[0],self.port),self.timeout)
        try:
            self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:
            raw.close();raise


def bls_value(document: dict, series: str, year: int, period: str) -> str | None:
    if document.get('status')!='REQUEST_SUCCEEDED':
        raise ValueError('BLS request did not succeed')
    rows=document.get('Results',{}).get('series')
    if not isinstance(rows,list) or len(rows)!=1 or rows[0].get('seriesID')!=series:
        raise ValueError('unexpected BLS series')
    matches=[r for r in rows[0].get('data',[]) if r.get('year')==str(year) and r.get('period')==period]
    if len(matches)>1:
        raise ValueError('ambiguous release values')
    if not matches:return None
    value=matches[0].get('value')
    if not isinstance(value,str) or len(value)>100 or not Decimal(value).is_finite():
        raise ValueError('nonfinite BLS value')
    return value


def fetch_bls(series: str, year: int, period: str, *, permit: bool=False) -> dict:
    if permit is not True:
        raise PermissionError('data-source license admission required before network access')
    if not re.fullmatch(r'[A-Z0-9]{4,30}',series) or type(year) is not int or not 1900<=year<=2200 or not re.fullmatch(r'M(0[1-9]|1[0-2])',period):
        raise ValueError('invalid exact series/period selector')
    c=PublicHTTPS('api.bls.gov',timeout=15,context=ssl.create_default_context())
    try:
        c.request('GET','/publicAPI/v1/timeseries/data/'+series,
                  headers={'Accept':'application/json','User-Agent':'FutureIntelligence/2.0.1 research'})
        response=c.getresponse();raw=response.read(2_000_001)
        if response.status!=200 or len(raw)>2_000_000:
            raise ValueError('BLS network response rejected')
        doc=strict_json(raw.decode('utf-8'));value=bls_value(doc,series,year,period)
        return dict(adapter='BLS_V1_EXACT_PERIOD',series=series,year=year,period=period,value_text=value,
                    has_target=value is not None,document_sha256=hashlib.sha256(raw).hexdigest(),
                    observed_at=datetime.now(timezone.utc).isoformat(),publication_time_verified=False,
                    first_release_verified=False,requires_blind_review=True)
    finally:c.close()


def release_question(template: dict, *, known_target: bool, now: datetime | None=None) -> dict:
    required={'series','year','period','forecast_deadline','outcome_not_before','resolution_deadline',
              'original_timezone','calendar_reference','calendar_sha256','bin_edges','unit','review_note'}
    if set(template)!=required or type(known_target) is not bool:
        raise ValueError('complete reviewed release template required')
    if known_target:
        raise ValueError('known result cannot become a prospective question')
    if not re.fullmatch(r'[A-Z0-9]{4,30}',template['series']) or type(template['year']) is not int or not re.fullmatch(r'M(0[1-9]|1[0-2])',template['period']):
        raise ValueError('invalid release identity')
    u=urlsplit(template['calendar_reference'])
    if u.scheme!='https' or u.hostname not in ('www.bls.gov','bls.gov') or u.username or u.password:
        raise ValueError('official calendar reference required')
    if not re.fullmatch(r'[0-9a-f]{64}',template['calendar_sha256']) or len(template['review_note'].strip())<10:
        raise ValueError('calendar snapshot and explicit review required')
    times=[utc(template[k]) for k in ('forecast_deadline','outcome_not_before','resolution_deadline')]
    if not utc(now or datetime.now(timezone.utc))<times[0]<times[1]<=times[2]:
        raise ValueError('prospective calendar ordering invalid')
    key=f"BLS:{template['series']}:{template['year']}:{template['period']}"
    spec=dict(title='First official release: '+key,event_definition='Value of the exact BLS series and reference period '+key,
              domain='MACRO',track='FAST',question_type='binned_numeric',event_cluster=key,
              original_timezone=template['original_timezone'],forecast_deadline=template['forecast_deadline'],
              outcome_not_before=template['outcome_not_before'],resolution_deadline=template['resolution_deadline'],
              resolution_rule='Use the original official release, not a silently revised time-series value. Calendar snapshot '+template['calendar_sha256'],
              data_revision_policy='FIRST_RELEASE',unit=template['unit'],bin_edges=template['bin_edges'])
    from future_intelligence.questions.spec import validate_spec
    return validate_spec(spec)


def supervise(step, *, cycles: int, interval: float, sleep=time.sleep) -> list[dict]:
    """One isolated worker, bounded iterations; no retry of ambiguous HTTP calls."""
    if type(cycles) is not int or not 1<=cycles<=10000 or not 1<=interval<=300:
        raise ValueError('bounded supervisor settings required')
    results=[]
    for n in range(cycles):
        try:result=step()
        except Exception:
            results.append({'status':'STOPPED_UNCERTAIN','reason':'NO_AUTOMATIC_REDISPATCH'});break
        results.append(result)
        if result.get('status') in ('BLOCKED','NO_REDISPATCH','STOPPED_UNCERTAIN'):
            break
        if n+1<cycles:sleep(interval)
    return results


def acceptance_gates(*, live_providers: list[str], real_tsa: bool, confirmed_outcomes: int, blind_review: bool) -> dict:
    remaining=[]
    for p in ('openai','anthropic','google'):
        if p not in live_providers:remaining.append('LIVE_API_'+p.upper())
    if not real_tsa:remaining.append('INDEPENDENT_EXTERNAL_TIMESTAMP')
    if confirmed_outcomes<1:remaining.append('REAL_PROSPECTIVE_RESOLUTION')
    if not blind_review:remaining.append('INDEPENDENT_BLIND_REVIEW')
    return {'production_acceptance':'BLOCKED' if remaining else 'REQUIRES_EVIDENCE_REVIEW',
            'outstanding_gates':remaining,'counts_are_not_proof_of_predictive_skill':True}
