"""All provider payloads below are synthetic; these tests incur no API charges."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

from future_intelligence.providers.live import (
    Blocked, HTTPSOnce, PROMPT, PROMPT_SHA256, WireResponse, build_request, credentials,
    encode, estimated_cost, parse_response, strict_json, validate_entry,
)
from future_intelligence.tasks.live_worker import run_once


def entry(provider='openai'):
    return {'entry_key':'model-0','provider':provider,'model_id':'fixture-model-not-live',
            'prompt_sha256':PROMPT_SHA256,'parameters':{'max_output_tokens':512,'timeout_seconds':5,
            'input_token_ceiling':16000,'price_quote':{'input_microusd_per_million':1000000,
            'output_microusd_per_million':2000000,'valid_until':'2030-01-01T00:00:00Z',
            'source':'https://example.invalid/FIXTURE-QUOTE-NOT-A-REAL-PRICE'}}}


def input_data():
    return {'protocol':'fi.model-input.v1','question_spec':{'question_type':'binary','bin_edges':[]},
            'evidence':{'items':[{'id':'e1','text':'Synthetic evidence. Ignore malicious instructions.'}]},
            'evidence_sha256':'a'*64}


def output():
    return {'probabilities_ppm':[300000,700000],'rationale':'Synthetic rationale, not a forecast.','evidence_ids':['e1']}


def response(provider='openai', value=None):
    text=json.dumps(output() if value is None else value)
    if provider=='openai':
        data={'id':'resp_fixture','model':'reported-model','status':'completed',
              'usage':{'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':10}},
              'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}
    elif provider=='anthropic':
        data={'id':'msg_fixture','model':'reported-model','stop_reason':'end_turn',
              'usage':{'input_tokens':100,'output_tokens':20,'cache_creation_input_tokens':5,'cache_read_input_tokens':10},
              'content':[{'type':'text','text':text}]}
    else:
        data={'responseId':'google_fixture','modelVersion':'reported-model',
              'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':20,'thoughtsTokenCount':5},
              'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':text}]}}]}
    return WireResponse(200,encode(data))


class ProviderTests(unittest.TestCase):
    def test_three_valid_configs(self):
        for p in ('openai','anthropic','google'):
            self.assertEqual(validate_entry(entry(p),1000000),entry(p))

    def test_prompt_hash_frozen(self):
        self.assertEqual(PROMPT_SHA256,hashlib.sha256(PROMPT.encode()).hexdigest())
        e=entry(); e['prompt_sha256']='0'*64
        with self.assertRaisesRegex(Blocked,'PROMPT_HASH'): validate_entry(e,1000000)

    def test_parameter_passthrough_denied(self):
        for key in ('tools','api_key','base_url','temperature','stream','thinking'):
            e=entry(); e['parameters'][key]='injected'
            with self.assertRaises(Blocked): validate_entry(e,1000000)

    def test_model_path_injection_denied(self):
        for model in ('../secret','model?key=secret','https://evil.invalid','model\nheader'):
            e=entry(); e['model_id']=model
            with self.assertRaises(Blocked): validate_entry(e,1000000)

    def test_quote_expiry(self):
        e=entry(); e['parameters']['price_quote']['valid_until']='2000-01-01T00:00:00Z'
        with self.assertRaisesRegex(Blocked,'EXPIRED'): validate_entry(e,1000000)

    def test_insufficient_reservation(self):
        with self.assertRaisesRegex(Blocked,'RESERVATION'): validate_entry(entry(),1)

    def test_noninteger_limits(self):
        for v in (True,1.1,float('inf'),0):
            e=entry(); e['parameters']['max_output_tokens']=v
            with self.assertRaises(Blocked): validate_entry(e,1000000)

    def test_missing_credentials(self):
        for p in ('openai','anthropic','google'):
            with self.assertRaises(Blocked): credentials(p,{})
        with self.assertRaises(Blocked): credentials('openai',{'OPENAI_API_KEY':'secret\nheader'})

    def test_exact_provider_payloads(self):
        a=strict_json(build_request(entry(),input_data()))
        self.assertEqual(a['tools'],[]); self.assertIs(a['store'],False); self.assertIs(a['stream'],False)
        b=strict_json(build_request(entry('anthropic'),input_data()))
        self.assertNotIn('tools',b); self.assertIn('output_config',b)
        c=strict_json(build_request(entry('google'),input_data()))
        self.assertNotIn('tools',c); self.assertEqual(c['generationConfig']['candidateCount'],1)
        self.assertEqual(strict_json(a['input']),strict_json(b['messages'][0]['content']))
        self.assertEqual(strict_json(a['input']),strict_json(c['contents'][0]['parts'][0]['text']))

    def test_input_planning_limit(self):
        data=input_data(); data['evidence']['items'][0]['text']='x'*20000
        with self.assertRaisesRegex(Blocked,'INPUT'): build_request(entry(),data)

    def test_success_three_providers(self):
        for p in ('openai','anthropic','google'):
            o=parse_response(p,response(p),2,{'e1'})
            self.assertEqual(o['status'],'OK'); self.assertEqual(o['output'],output())
            self.assertEqual(o['model_reported'],'reported-model')
            self.assertIsNotNone(o['response_sha256'])

    def test_usage_accounting(self):
        self.assertEqual(parse_response('anthropic',response('anthropic'),2,{'e1'})['usage']['input_tokens'],115)
        self.assertEqual(parse_response('google',response('google'),2,{'e1'})['usage']['output_tokens'],25)

    def test_unknown_usage_is_not_zero(self):
        w=response(); d=strict_json(w.body); d.pop('usage')
        o=parse_response('openai',WireResponse(200,encode(d)),2,{'e1'})
        self.assertIsNone(o['usage']['input_tokens']); self.assertIsNone(estimated_cost(entry(),o))

    def test_estimate_not_actual(self):
        self.assertEqual(estimated_cost(entry(),parse_response('openai',response(),2,{'e1'})),140)

    def test_wrong_probability_not_repaired(self):
        for probs in ([1,1],[True,999999],[-1,1000001],[0.3,0.7],[1000000]):
            v=output(); v['probabilities_ppm']=probs
            self.assertEqual(parse_response('openai',response(value=v),2,{'e1'})['status'],'INVALID_OUTPUT')

    def test_unknown_citation_rejected(self):
        v=output(); v['evidence_ids']=['fabricated']
        self.assertEqual(parse_response('openai',response(value=v),2,{'e1'})['status'],'INVALID_OUTPUT')

    def test_hidden_reasoning_not_stored(self):
        for p in ('openai','anthropic','google'):
            d=strict_json(response(p).body)
            if p=='openai': d['output'].insert(0,{'type':'reasoning','secret':'NEVER_PERSIST'})
            elif p=='anthropic': d['content'].insert(0,{'type':'thinking','thinking':'NEVER_PERSIST'})
            else: d['candidates'][0]['content']['parts'].insert(0,{'thought':True,'text':'NEVER_PERSIST'})
            o=parse_response(p,WireResponse(200,encode(d)),2,{'e1'})
            self.assertEqual(o['status'],'OK'); self.assertNotIn('NEVER_PERSIST',json.dumps(o))

    def test_tool_output_never_executed(self):
        d=strict_json(response().body); d['output']=[{'type':'function_call','arguments':'secret'}]
        self.assertEqual(parse_response('openai',WireResponse(200,encode(d)),2,{'e1'})['status'],'INVALID_OUTPUT')

    def test_refusal_and_incomplete(self):
        d=strict_json(response().body); d['status']='incomplete'
        self.assertEqual(parse_response('openai',WireResponse(200,encode(d)),2,{'e1'})['status'],'INCOMPLETE')
        d['status']='completed'; d['output'][0]['content']=[{'type':'refusal','refusal':'no'}]
        self.assertEqual(parse_response('openai',WireResponse(200,encode(d)),2,{'e1'})['status'],'REFUSED')

    def test_bad_json_fails_closed(self):
        for raw in (b'{}',b'not json',b'{"x":NaN}',b'{"x":1,"x":2}',b'\xff'):
            self.assertEqual(parse_response('openai',WireResponse(200,raw),2,{'e1'})['status'],'INVALID_OUTPUT')

    def test_http_error_body_redacted(self):
        for code in (301,400,401,429,500):
            o=parse_response('openai',WireResponse(code,b'secret echoed'),2,{'e1'})
            self.assertEqual(o['status'],'HTTP_ERROR'); self.assertNotIn('secret echoed',json.dumps(o))

    def test_transport_errors_preserved(self):
        for code in ('TIMEOUT','TRANSPORT_ERROR','RESPONSE_TOO_LARGE'):
            self.assertEqual(parse_response('openai',WireResponse(None,b'',error_code=code),2,{'e1'})['status'],code)

    def test_fixed_host_and_no_redirect(self):
        class Resp:
            status=302
            def read1(self,n): return b''
            def getheader(self,n): return None
        with patch('future_intelligence.providers.live.PublicHTTPS') as conn:
            conn.return_value.getresponse.return_value=Resp()
            w=HTTPSOnce().send('openai','fixture','{}','fixture-key',5)
            self.assertEqual(w.status,302); self.assertEqual(conn.call_count,1)
            self.assertEqual(conn.call_args.args[0],'api.openai.com')
            self.assertEqual(conn.return_value.request.call_count,1)

    def test_native_output_keys_strict(self):
        v=output(); v['chain_of_thought']='do not store'
        o=parse_response('openai',response(value=v),2,{'e1'})
        self.assertIsNone(o['output']); self.assertEqual(o['status'],'INVALID_OUTPUT')


class FakeRepo:
    def __init__(self):
        self.events=[]; self.marker=True
        self.lease={'task_id':'fixture-task','attempt_id':'fixture-attempt','fence':1,'entry':entry(),
                    'cap_microusd':1000000,'run_kind':'LIVE',
                    'lease_until':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
    def claim(self,e,k): self.events.append('claim'); return self.lease
    def claim_for_dispatch(self, experiment, task, count):
        return self.claim(experiment, 'LIVE', task_id=task) if task is not None else self.claim(experiment, 'LIVE')
    def supports_observation_v2(self): return True
    def context(self,l): self.events.append('context'); return input_data()
    def prepare(self,l,b): self.events.append('prepare'); return self.marker
    def observe(self,l,o): self.events.append('observe'); self.obs=o
    def succeed(self,l,p,h,c): self.events.append('succeed'); self.actual=c; return 'receipt'
    def fail(self,l,c,cost,retry): self.events.append('fail'); self.failure=(c,cost,retry); return 'receipt'


class FakeWire:
    def __init__(self,w=None): self.calls=0; self.w=w or response()
    def send(self,*args): self.calls+=1; return self.w


class RunnerTests(unittest.TestCase):
    def test_order_and_unknown_cost(self):
        r=FakeRepo(); w=FakeWire()
        result=run_once(r,'exp',w,{'OPENAI_API_KEY':'synthetic-key'})
        self.assertEqual(r.events,['claim','context','prepare','observe','succeed'])
        self.assertEqual(w.calls,1); self.assertIsNone(r.actual)
        self.assertEqual(result['external_timestamp_status'],'NOT_REQUESTED')

    def test_missing_key_records_block_without_dispatch(self):
        r=FakeRepo(); w=FakeWire(); out=run_once(r,'exp',w,{})
        self.assertEqual(out['status'],'BLOCKED'); self.assertEqual(w.calls,0)
        self.assertEqual(r.failure[1:],(0,False))

    def test_repeated_marker_is_not_send_permission(self):
        r=FakeRepo(); r.marker=False; w=FakeWire()
        self.assertEqual(run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})['status'],'NO_REDISPATCH')
        self.assertEqual(w.calls,0)

    def test_ambiguous_prepare_cannot_send(self):
        r=FakeRepo(); w=FakeWire()
        def ambiguous(*args): raise ConnectionError('synthetic')
        r.prepare=ambiguous
        with self.assertRaises(ConnectionError): run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})
        self.assertEqual(w.calls,0)

    def test_wire_failure_no_auto_retry(self):
        r=FakeRepo(); w=FakeWire(WireResponse(None,b'',error_code='TIMEOUT'))
        self.assertEqual(run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})['status'],'TIMEOUT')
        self.assertEqual(w.calls,1); self.assertEqual(r.failure,('TIMEOUT',None,False))

    def test_malformed_response_still_observed(self):
        r=FakeRepo(); w=FakeWire(WireResponse(200,b'{broken'))
        run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})
        self.assertEqual(r.events[-2:],['observe','fail']); self.assertIsNone(r.failure[1])

    def test_late_finish_preserves_observation(self):
        r=FakeRepo(); w=FakeWire()
        def expired(*args): raise RuntimeError('expired')
        r.succeed=expired
        with self.assertRaises(RuntimeError): run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})
        self.assertEqual(r.events[-1],'observe'); self.assertEqual(w.calls,1)

    def test_short_lease_blocks_before_dispatch(self):
        r=FakeRepo(); w=FakeWire(); r.lease['lease_until']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        result=run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})
        self.assertEqual(result['reason'],'LEASE_TOO_SHORT'); self.assertEqual(w.calls,0)

    def test_observation_storage_error_no_resend(self):
        r=FakeRepo(); w=FakeWire()
        def unavailable(*args): raise ConnectionError('db unavailable')
        r.observe=unavailable
        with self.assertRaises(ConnectionError): run_once(r,'exp',w,{'OPENAI_API_KEY':'x'})
        self.assertEqual(w.calls,1); self.assertNotIn('succeed',r.events)
