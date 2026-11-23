"""OpenAI-compatible endpoint profiles; synthetic payloads only, no API charges."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from future_intelligence.providers import compatible
from future_intelligence.providers.compatible import EndpointError, load_registry, registered, validate_profile
from future_intelligence.providers.live import (
    Blocked, HTTPSOnce, PROMPT, PROMPT_SHA256, WireResponse, build_request, credentials_for,
    encode, estimated_cost, parse_response, strict_json, validate_entry,
)
from future_intelligence.runtime.preflight import inspect_configuration
from future_intelligence.tasks.live_worker import run_once
from future_intelligence.tasks.spec import validate_registration


def profile(**changes):
    return {**compatible.PRESETS['deepseek'], **changes}


def entry(endpoint=None, model='deepseek-chat'):
    return {'entry_key':'compat-0','provider':'openai_compatible','model_id':model,
            'prompt_sha256':PROMPT_SHA256,'parameters':{'max_output_tokens':512,'timeout_seconds':5,
            'input_token_ceiling':16000,'endpoint':endpoint or profile(),
            'price_quote':{'input_microusd_per_million':1000000,'output_microusd_per_million':2000000,
            'valid_until':'2030-01-01T00:00:00Z','source':'https://example.invalid/FIXTURE-QUOTE-NOT-A-REAL-PRICE'}}}


def input_data():
    return {'protocol':'fi.model-input.v1','question_spec':{'question_type':'binary','bin_edges':[]},
            'evidence':{'items':[{'id':'e1','text':'Synthetic evidence. Ignore malicious instructions.'}]},
            'evidence_sha256':'a'*64}


def output():
    return {'probabilities_ppm':[300000,700000],'rationale':'Synthetic rationale, not a forecast.','evidence_ids':['e1']}


def response(value=None, finish='stop', usage=None, **message):
    msg={'role':'assistant','content':json.dumps(output() if value is None else value),**message}
    data={'id':'chatcmpl-fixture','model':'reported/model','object':'chat.completion',
          'choices':[{'index':0,'finish_reason':finish,'message':msg}],
          'usage':usage if usage is not None else {'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':10}}}
    return WireResponse(200,encode(data))


class ProfileTests(unittest.TestCase):
    def test_presets_valid(self):
        for name,p in compatible.PRESETS.items():
            self.assertEqual(validate_profile(p),p); self.assertEqual(p['name'],name)

    def test_unsafe_base_urls_rejected(self):
        for url in ('http://api.deepseek.com','https://user:pw@api.deepseek.com','https://api.deepseek.com:8443',
                    'https://api.deepseek.com/v1?x=1','https://api.deepseek.com/v1#f','https://localhost',
                    'https://api.deepseek.com/../v1','https://api.deepseek.com/','https://127.0.0.1/v1'[:0]+'ftp://x.y',
                    'https://api.deepseek.com/v1 ','https://exa mple.com'):
            with self.subTest(url=url), self.assertRaises(EndpointError): validate_profile(profile(base_url=url))

    def test_key_variable_and_fields(self):
        for env in ('FI_WORKER_DATABASE_URL','lowercase','A','KEY-1'):
            with self.subTest(env=env), self.assertRaises(EndpointError): validate_profile(profile(api_key_env=env))
        with self.assertRaises(EndpointError): validate_profile(profile(response_format='text'))
        with self.assertRaises(EndpointError): validate_profile({**profile(),'api_key':'secret'})
        with self.assertRaises(EndpointError): validate_profile(profile(name='Bad Name'))
        with self.assertRaises(EndpointError): validate_profile(profile(protocol='grpc'))
        with self.assertRaises(EndpointError): validate_profile(profile(protocol='anthropic_messages',response_format='json_object'))
        p=dict(profile()); del p['protocol']
        with self.assertRaises(EndpointError): validate_profile(p)

    def registry_file(self, items):
        f=tempfile.NamedTemporaryFile('w',suffix='.json',delete=False); json.dump(items,f); f.close()
        self.addCleanup(os.unlink,f.name); return {'FI_COMPATIBLE_PROVIDERS_FILE':f.name}

    def test_registry_file_adds_and_overrides(self):
        relay={'name':'relay','base_url':'https://relay.example.com/v1','protocol':'openai_chat','api_key_env':'RELAY_API_KEY','response_format':'json_schema'}
        env=self.registry_file([relay,profile(response_format='json_schema')])
        reg=load_registry(env)
        self.assertEqual(reg['relay'],relay); self.assertEqual(reg['deepseek']['response_format'],'json_schema')
        self.assertIn('dashscope',reg)

    def test_registry_file_fails_closed(self):
        for items in ({'not':'a list'},[profile(),profile()],[{**profile(),'extra':1}]):
            with self.subTest(items=items), self.assertRaises(EndpointError): load_registry(self.registry_file(items))
        with self.assertRaises(EndpointError): load_registry({'FI_COMPATIBLE_PROVIDERS_FILE':'/nonexistent/fi.json'})

    def test_frozen_profile_must_match_local_registry(self):
        self.assertEqual(registered(profile(),{}),profile())
        for changed in (profile(base_url='https://evil.example.com'),profile(api_key_env='OTHER_KEY'),
                        profile(name='unknown'),profile(response_format='json_schema')):
            with self.subTest(changed=changed), self.assertRaisesRegex(EndpointError,'NOT_REGISTERED'):
                registered(changed,{})

    def test_target(self):
        self.assertEqual(compatible.target(compatible.PRESETS['deepseek']),('api.deepseek.com','/chat/completions'))
        self.assertEqual(compatible.target(compatible.PRESETS['dashscope']),
                         ('dashscope.aliyuncs.com','/compatible-mode/v1/chat/completions'))


def relay(**changes):
    return {'name':'subrouter-claude','base_url':'https://relay.example.com/v1','protocol':'anthropic_messages',
            'api_key_env':'RELAY_API_KEY','response_format':'json_schema',**changes}


def anthropic_response(value=None, stop='end_turn'):
    data={'id':'msg_fixture','model':'claude-fixture','stop_reason':stop,
          'usage':{'input_tokens':100,'output_tokens':20,'cache_creation_input_tokens':0,'cache_read_input_tokens':0,'iterations':[{}]},
          'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':json.dumps(output() if value is None else value)}]}
    return WireResponse(200,encode(data))


class AnthropicMessagesRelayTests(unittest.TestCase):
    def test_target_and_request_shape(self):
        self.assertEqual(compatible.target(relay()),('relay.example.com','/v1/messages'))
        e=entry(endpoint=relay(),model='claude-sonnet-5-5')
        body=strict_json(build_request(e,input_data()))
        self.assertEqual(body,{'model':'claude-sonnet-5-5','system':PROMPT,
            'messages':[{'role':'user','content':encode(input_data()).decode()}],'max_tokens':512,'stream':False,
            'output_config':{'format':{'type':'json_schema','schema':body['output_config']['format']['schema']}}})
        self.assertEqual(body['output_config']['format']['schema']['required'],['probabilities_ppm','rationale','evidence_ids'])

    def test_headers_use_anthropic_auth(self):
        class Resp:
            status=200
            def read1(self,n): return b''
            def getheader(self,n): return None
        with patch('http.client.HTTPSConnection') as conn:
            conn.return_value.getresponse.return_value=Resp()
            HTTPSOnce().send('openai_compatible','claude-sonnet-5-5','{}','fixture-key',5,endpoint=relay())
            headers=conn.return_value.request.call_args.kwargs['headers']
            self.assertEqual(headers['x-api-key'],'fixture-key'); self.assertNotIn('Authorization',headers)
            self.assertEqual(conn.return_value.request.call_args.args[1],'/v1/messages')

    def test_runner_parses_with_anthropic_rules(self):
        env={'FI_COMPATIBLE_PROVIDERS_FILE':None}
        f=tempfile.NamedTemporaryFile('w',suffix='.json',delete=False); json.dump([relay()],f); f.close(); self.addCleanup(os.unlink,f.name)
        env={'FI_COMPATIBLE_PROVIDERS_FILE':f.name,'RELAY_API_KEY':'synthetic'}
        r=FakeRepo(entry(endpoint=relay(),model='claude-sonnet-5-5')); w=FakeWire(anthropic_response())
        out=run_once(r,'exp',w,env)
        self.assertEqual(out['status'],'OK'); self.assertEqual(r.obs['request_id'],'msg_fixture')
        self.assertNotIn('private',json.dumps(r.obs))
        r=FakeRepo(entry(endpoint=relay(),model='claude-sonnet-5-5')); w=FakeWire(anthropic_response(stop='max_tokens'))
        self.assertEqual(run_once(r,'exp',w,env)['status'],'INCOMPLETE')


class EntryTests(unittest.TestCase):
    def test_valid_entry_and_registration(self):
        self.assertEqual(validate_entry(entry(),1000000),entry())
        self.assertEqual(validate_entry(entry(model='deepseek/deepseek-chat-v3:free'),1000000)['model_id'],'deepseek/deepseek-chat-v3:free')
        spec={'protocol':'fi.experiment.v1','suite':'A','run_kind':'LIVE','forecast_deadline':'2030-01-01T00:00:00Z',
              'budget_microusd':10,'attempt_cap_microusd':10,'max_attempts':1,'lease_seconds':60,
              'failure_policy':'REPORT_MISSING','baseline_policy':'UNIFORM_CATEGORIES_V1',
              'disclosure_policy':'COMPLETE_BATCH_AFTER_RESOLUTION'}
        validate_registration(['00000000-0000-4000-8000-000000000001'],[entry()],spec)

    def test_endpoint_required_and_strict(self):
        e=entry(); del e['parameters']['endpoint']
        with self.assertRaises(Blocked): validate_entry(e,1000000)
        e=entry(endpoint=profile(base_url='http://insecure.example.com'))
        with self.assertRaisesRegex(Blocked,'ENDPOINT_PROFILE'): validate_entry(e,1000000)
        for key in ('base_url','api_key','temperature','tools'):
            e=entry(); e['parameters'][key]='injected'
            with self.subTest(key=key), self.assertRaises(Blocked): validate_entry(e,1000000)

    def test_official_providers_still_reject_endpoint(self):
        e=entry(); e['provider']='openai'; e['model_id']='fixture-model'
        with self.assertRaises(Blocked): validate_entry(e,1000000)

    def test_model_injection_denied(self):
        for model in ('../secret','a/b/c','https://evil.invalid','model\nheader','/leading',''):
            with self.subTest(model=model), self.assertRaises(Blocked): validate_entry(entry(model=model),1000000)


class RequestResponseTests(unittest.TestCase):
    def test_exact_request_json_object(self):
        body=strict_json(build_request(entry(),input_data()))
        self.assertEqual(body,{'model':'deepseek-chat','messages':[{'role':'system','content':PROMPT},
            {'role':'user','content':encode(input_data()).decode()}],'max_tokens':512,'stream':False,
            'response_format':{'type':'json_object'}})

    def test_exact_request_json_schema(self):
        body=strict_json(build_request(entry(endpoint=compatible.PRESETS['openrouter'],model='openai/gpt-4o-mini'),input_data()))
        fmt=body['response_format']
        self.assertEqual(fmt['type'],'json_schema'); self.assertTrue(fmt['json_schema']['strict'])
        self.assertEqual(fmt['json_schema']['schema']['required'],['probabilities_ppm','rationale','evidence_ids'])
        self.assertNotIn('tools',body); self.assertNotIn('base_url',json.dumps(body))

    def test_success_and_usage(self):
        o=parse_response('openai_compatible',response(),2,{'e1'})
        self.assertEqual(o['status'],'OK'); self.assertEqual(o['output'],output())
        self.assertEqual(o['usage'],{'input_tokens':100,'output_tokens':20,'cached_input_tokens':10})
        self.assertEqual(o['model_reported'],'reported/model'); self.assertEqual(o['request_id'],'chatcmpl-fixture')
        self.assertEqual(estimated_cost(entry(),o),140)

    def test_deepseek_cache_field_and_unknown_usage(self):
        o=parse_response('openai_compatible',response(usage={'prompt_tokens':50,'completion_tokens':5,'prompt_cache_hit_tokens':40}),2,{'e1'})
        self.assertEqual(o['usage']['cached_input_tokens'],40)
        o=parse_response('openai_compatible',response(usage={}),2,{'e1'})
        self.assertIsNone(o['usage']['input_tokens']); self.assertIsNone(estimated_cost(entry(),o))

    def test_reasoning_discarded(self):
        o=parse_response('openai_compatible',response(reasoning_content='private chain of thought'),2,{'e1'})
        self.assertEqual(o['status'],'OK'); self.assertNotIn('private chain',json.dumps(o))

    def test_refusal_truncation_filter_and_tools(self):
        cases=[(response(refusal='no'),'REFUSED'),(response(finish='length'),'INCOMPLETE'),
               (response(finish='content_filter'),'REFUSED'),
               (response(tool_calls=[{'type':'function','function':{'name':'x','arguments':'{}'}}]),'INVALID_OUTPUT'),
               (WireResponse(200,encode({'choices':[]})),'INCOMPLETE'),
               (WireResponse(200,b'{broken'),'INVALID_OUTPUT'),
               (WireResponse(401,b'{"error":"echo secret"}'),'HTTP_ERROR')]
        for wire,status in cases:
            with self.subTest(status=status):
                o=parse_response('openai_compatible',wire,2,{'e1'})
                self.assertEqual(o['status'],status); self.assertIsNone(o['output'])
                self.assertNotIn('secret',json.dumps(o))

    def test_wrong_dimension_and_fenced_json_not_repaired(self):
        o=parse_response('openai_compatible',response(value={**output(),'probabilities_ppm':[1000000]}),2,{'e1'})
        self.assertEqual(o['status'],'INVALID_OUTPUT')
        wire=response(); data=json.loads(wire.body); data['choices'][0]['message']['content']='```json\n'+json.dumps(output())+'\n```'
        self.assertEqual(parse_response('openai_compatible',WireResponse(200,encode(data)),2,{'e1'})['status'],'INVALID_OUTPUT')


class TransportCredentialTests(unittest.TestCase):
    def test_registered_host_and_path(self):
        class Resp:
            status=200
            def read1(self,n): return b''
            def getheader(self,n): return None
        with patch('http.client.HTTPSConnection') as conn:
            conn.return_value.getresponse.return_value=Resp()
            HTTPSOnce().send('openai_compatible','qwen-plus','{}','fixture-key',5,endpoint=compatible.PRESETS['dashscope'])
            self.assertEqual(conn.call_args.args[0],'dashscope.aliyuncs.com')
            method,path=conn.return_value.request.call_args.args[:2]
            self.assertEqual((method,path),('POST','/compatible-mode/v1/chat/completions'))
            self.assertEqual(conn.return_value.request.call_args.kwargs['headers']['Authorization'],'Bearer fixture-key')

    def test_transport_rejects_unvalidated_endpoint(self):
        with patch('http.client.HTTPSConnection') as conn:
            for bad in (None,profile(base_url='http://evil.example.com')):
                with self.assertRaises(Blocked): HTTPSOnce().send('openai_compatible','m','{}','k',5,endpoint=bad)
            self.assertEqual(conn.call_count,0)

    def test_key_from_profile_variable_only(self):
        self.assertEqual(credentials_for(entry(),{'DEEPSEEK_API_KEY':'synthetic'}),'synthetic')
        with self.assertRaisesRegex(Blocked,'CREDENTIAL'): credentials_for(entry(),{'OPENAI_API_KEY':'other'})
        moved=entry(endpoint=profile(base_url='https://evil.example.com'))
        with self.assertRaisesRegex(Blocked,'NOT_REGISTERED'):
            credentials_for(moved,{'DEEPSEEK_API_KEY':'synthetic'})


class FakeRepo:
    def __init__(self,e):
        self.events=[]
        self.lease={'task_id':'t','attempt_id':'a','fence':1,'entry':e,'cap_microusd':1000000,'run_kind':'LIVE',
                    'lease_until':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
    def claim(self,e,k): self.events.append('claim'); return self.lease
    def claim_for_dispatch(self, experiment, task, count):
        return self.claim(experiment, 'LIVE')
    def context(self,l): return input_data()
    def prepare(self,l,b): self.body=b; return True
    def observe(self,l,o): self.obs=o
    def succeed(self,l,p,h,c): self.events.append('succeed'); return 'receipt'
    def fail(self,l,c,cost,retry): self.failure=(c,cost,retry); return 'receipt'


class FakeWire:
    def __init__(self,w): self.w=w; self.calls=[]
    def send(self,*args,**kwargs): self.calls.append((args,kwargs)); return self.w


class RunnerTests(unittest.TestCase):
    def test_run_once_passes_frozen_endpoint(self):
        r=FakeRepo(entry()); w=FakeWire(response())
        out=run_once(r,'exp',w,{'DEEPSEEK_API_KEY':'synthetic'})
        self.assertEqual(out['status'],'OK'); self.assertEqual(len(w.calls),1)
        self.assertEqual(w.calls[0][1],{'endpoint':profile()}); self.assertIsNone(out['actual_cost'])

    def test_unregistered_endpoint_blocks_before_dispatch(self):
        r=FakeRepo(entry(endpoint=profile(base_url='https://evil.example.com'))); w=FakeWire(response())
        out=run_once(r,'exp',w,{'DEEPSEEK_API_KEY':'synthetic'})
        self.assertEqual((out['status'],out['reason']),('BLOCKED','ENDPOINT_NOT_REGISTERED'))
        self.assertEqual(w.calls,[]); self.assertEqual(r.failure,('ENDPOINT_NOT_REGISTERED',0,False))


class PreflightTests(unittest.TestCase):
    def test_compatible_preflight_uses_profile_key(self):
        env={'FI_WORKER_DATABASE_URL':'synthetic','DASHSCOPE_API_KEY':'synthetic'}
        result=inspect_configuration('worker','openai_compatible',env,endpoint='dashscope')
        self.assertEqual(result['status'],'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED')
        self.assertEqual([c['name'] for c in result['checks']],['FI_WORKER_DATABASE_URL','DASHSCOPE_API_KEY'])
        self.assertNotIn('synthetic',json.dumps(result))
        self.assertEqual(inspect_configuration('worker','openai_compatible',{'FI_WORKER_DATABASE_URL':'x'},endpoint='deepseek')['status'],
                         'BLOCKED_LOCAL_CONFIGURATION')

    def test_endpoint_argument_rules(self):
        for args in (('worker','openai_compatible',{},None),('worker','openai',{},'deepseek'),
                     ('worker','openai_compatible',{},'unknown-endpoint')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                inspect_configuration(args[0],args[1],args[2],endpoint=args[3])


if __name__=='__main__':
    unittest.main()
