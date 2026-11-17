"""Regression of the reported 4-boundary/4-probability defect. No API calls."""
import copy
from decimal import Decimal
import hashlib
import json
import unittest

from future_intelligence.providers.live import (
    Blocked, CURRENT_PROMPT_SHA256, PROMPT, PROMPT_SHA256, PROMPT_V2,
    PROMPT_V2_SHA256, WireResponse, build_request, encode, output_schema,
    output_schema_v2, parse_response, strict_json, validate_entry,
)
from future_intelligence.questions.spec import answer_contract, outcome_bin


def question(edges=None):
    return {'title':'Synthetic contract regression', 'event_definition':'Test only; not a prediction.',
            'domain':'MACRO','track':'FAST','question_type':'binary' if edges is None else 'binned_numeric',
            'event_cluster':'FIXTURE-CONTRACT','original_timezone':'UTC',
            'forecast_deadline':'2030-01-01T00:00:00Z','outcome_not_before':'2030-01-02T00:00:00Z',
            'resolution_deadline':'2030-01-03T00:00:00Z','resolution_rule':'Synthetic outcome only',
            'data_revision_policy':'FIRST_RELEASE','unit':'percentage points','bin_edges':[] if edges is None else edges}


def entry(provider='openai', legacy=False):
    return {'entry_key':'model-0','provider':provider,'model_id':'fixture-model-not-live',
            'prompt_sha256':PROMPT_SHA256 if legacy else CURRENT_PROMPT_SHA256,
            'parameters':{'max_output_tokens':512,'timeout_seconds':5,'input_token_ceiling':30000,
                'price_quote':{'input_microusd_per_million':1000000,'output_microusd_per_million':2000000,
                               'valid_until':'2099-01-01T00:00:00Z',
                               'source':'https://example.invalid/FIXTURE-NOT-A-PRICE'}}}


def context(edges=None, legacy=False):
    result={'protocol':'fi.model-input.v1' if legacy else 'fi.model-input.v2',
            'question_spec':question(edges), 'evidence':{'items':[{'id':'e1','text':'Synthetic.'}]},
            'evidence_sha256':'a'*64}
    if not legacy: result['answer_contract']=answer_contract(result['question_spec'])
    return result


def wire(probs, provider='openai'):
    output={'probabilities_ppm':probs,'rationale':'Synthetic contract check.','evidence_ids':['e1']}
    text=json.dumps(output)
    if provider=='openai':
        data={'id':'resp_fixture','model':'fixture','status':'completed',
              'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}
    elif provider=='anthropic':
        data={'id':'msg_fixture','model':'fixture','stop_reason':'end_turn','content':[{'type':'text','text':text}]}
    else:
        data={'responseId':'fixture','modelVersion':'fixture',
              'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':text}]}}]}
    return WireResponse(200,encode(data))


class AnswerContractTests(unittest.TestCase):
    def test_binary_order_is_no_yes(self):
        c=answer_contract(question())
        self.assertEqual(c['categories'],[{'index':0,'label':'No'},{'index':1,'label':'Yes'}])
        self.assertEqual(c['category_count'],2)
        self.assertEqual(c['probability_total_ppm'],1000000)

    def test_four_boundaries_are_five_explicit_categories(self):
        c=answer_contract(question(['1','2','3','4']))
        self.assertEqual(c['category_count'],5)
        self.assertEqual([x['label'] for x in c['categories']],
                         ['x < 1','1 <= x < 2','2 <= x < 3','3 <= x < 4','x >= 4'])
        self.assertIsNone(c['categories'][0]['lower'])
        self.assertIsNone(c['categories'][-1]['upper'])

    def test_various_counts_and_exact_edges_match_scorer(self):
        for count in (1,2,4,7,100):
            edges=[str(i) for i in range(count)]
            c=answer_contract(question(edges))
            self.assertEqual(c['category_count'],count+1)
            for i,edge in enumerate(edges):
                self.assertEqual(outcome_bin(edge,edges),i+1)
                self.assertEqual(c['categories'][i+1]['lower'],edge)
                self.assertTrue(c['categories'][i+1]['lower_inclusive'])
                self.assertFalse(c['categories'][i]['upper_inclusive'])
            self.assertEqual(outcome_bin('-0.0001',edges),0)
            self.assertEqual(outcome_bin(str(count+1),edges),count)

    def test_precision_exponents_and_units_preserved(self):
        edges=['-1e-20','0.000000000000000000001','2.000000000000000000001']
        c=answer_contract(question(edges))
        self.assertEqual([x['upper'] for x in c['categories'][:-1]],edges)
        self.assertEqual(c['unit'],'percentage points')
        self.assertEqual(outcome_bin(edges[1],edges),2)

    def test_invalid_spec_rejected_before_request(self):
        for edges in ([], ['1','1'], ['2','1'], ['NaN'], [True], [1], ['Infinity']):
            with self.subTest(edges=edges), self.assertRaises(ValueError): answer_contract(question(edges))

    def test_inputs_not_mutated_or_shared(self):
        q=question(['1','2']); before=copy.deepcopy(q)
        a=answer_contract(q); a['categories'][0]['label']='tampered'
        self.assertEqual(q,before)
        self.assertNotEqual(answer_contract(q)['categories'][0]['label'],'tampered')

    def test_new_hash_without_rewriting_legacy(self):
        self.assertEqual(PROMPT_SHA256,'8d788690bf2c5b7c53a1f7d669345132d7a4c312c6c51a05da38db532f1073a4')
        self.assertEqual(PROMPT_V2_SHA256,'3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da')
        self.assertEqual(hashlib.sha256(PROMPT.encode()).hexdigest(),PROMPT_SHA256)
        self.assertEqual(hashlib.sha256(PROMPT_V2.encode()).hexdigest(),CURRENT_PROMPT_SHA256)
        self.assertNotEqual(PROMPT_SHA256,CURRENT_PROMPT_SHA256)

    def test_v1_and_v2_configs_both_validate(self):
        for p in ('openai','anthropic','google'):
            for legacy in (False,True): self.assertEqual(validate_entry(entry(p,legacy),1000000),entry(p,legacy))

    def test_native_lengths_where_supported(self):
        for p in ('openai','google'):
            for k in (2,3,5,8,101):
                a=output_schema_v2(k,p)['properties']['probabilities_ppm']
                self.assertEqual((a['minItems'],a['maxItems']),(k,k))
                self.assertEqual(a['items'],{'type':'integer','minimum':0,'maximum':1000000})

    def test_anthropic_subset_no_false_native_guarantee(self):
        a=output_schema_v2(5,'anthropic')['properties']['probabilities_ppm']
        self.assertNotIn('minItems',a); self.assertNotIn('maxItems',a)
        self.assertEqual(a['items'],{'type':'integer'})
        self.assertIn('Exactly 5',a['description'])

    def test_schema_cannot_accept_invalid_counts_or_unknown_route(self):
        for k in (True,1,0,-1,102,5.0,None):
            with self.assertRaises(Blocked): output_schema_v2(k,'openai')
        with self.assertRaises(Blocked): output_schema_v2(5,'relay')

    def test_v2_actual_three_provider_request_bodies(self):
        contexts=[]
        for p in ('openai','anthropic','google'):
            body=strict_json(build_request(entry(p),context(['1','2','3','4'])))
            if p=='openai':
                inner=body['input']; prompt=body['instructions']; schema=body['text']['format']['schema']
            elif p=='anthropic':
                inner=body['messages'][0]['content']; prompt=body['system']; schema=body['output_config']['format']['schema']
            else:
                inner=body['contents'][0]['parts'][0]['text']; prompt=body['systemInstruction']['parts'][0]['text']
                schema=body['generationConfig']['responseJsonSchema']
            contexts.append(strict_json(inner))
            self.assertEqual(prompt,PROMPT_V2)
            self.assertEqual(schema,output_schema_v2(5,p))
        self.assertEqual(contexts[0],contexts[1]); self.assertEqual(contexts[1],contexts[2])

    def test_malformed_or_tampered_contract_fails_before_dispatch(self):
        good=context(['1','2','3','4'])
        for mutate in ('count','order','boundary','unit','total','boolean_count','missing','extra','protocol'):
            data=copy.deepcopy(good)
            if mutate=='count': data['answer_contract']['category_count']=4
            elif mutate=='order': data['answer_contract']['categories'].reverse()
            elif mutate=='boundary': data['answer_contract']['categories'][1]['lower_inclusive']=False
            elif mutate=='unit': data['answer_contract']['unit']='different'
            elif mutate=='total': data['answer_contract']['probability_total_ppm']=100
            elif mutate=='boolean_count': data['answer_contract']['category_count']=True
            elif mutate=='missing': del data['answer_contract']
            elif mutate=='extra': data['unregistered']='injection'
            else: data['protocol']='fi.model-input.v1'
            with self.subTest(mutate=mutate),self.assertRaisesRegex(Blocked,'ANSWER_CONTRACT'): build_request(entry(),data)

    def test_v1_keeps_old_wire_contract(self):
        b=strict_json(build_request(entry(legacy=True),context(legacy=True)))
        self.assertEqual(b['instructions'],PROMPT)
        self.assertEqual(b['text']['format']['schema'],output_schema())
        self.assertNotIn('answer_contract',strict_json(b['input']))

    def test_v1_v2_mismatch_not_silently_repaired(self):
        with self.assertRaises(Blocked): build_request(entry(legacy=True),context())
        with self.assertRaises(Blocked): build_request(entry(),context(legacy=True))

    def test_original_four_probability_failure_still_rejected(self):
        for p in ('openai','anthropic','google'):
            self.assertEqual(parse_response(p,wire([250000]*4,p),5,{'e1'})['status'],'INVALID_OUTPUT')
            self.assertEqual(parse_response(p,wire([200000]*5,p),5,{'e1'})['status'],'OK')

    def test_no_normalization_or_extra_probability_repair(self):
        for probs in ([200001]*5,[200000]*6,[0.2]*5,[True,0,0,0,999999],[-1,250000,250000,250000,250001]):
            self.assertEqual(parse_response('openai',wire(probs),5,{'e1'})['status'],'INVALID_OUTPUT')

    def test_native_bounds_do_not_replace_semantic_checks(self):
        self.assertEqual(parse_response('openai',wire([0]*5),5,{'e1'})['status'],'INVALID_OUTPUT')
        self.assertEqual(parse_response('openai',wire([1000000,0,0,0,0]),5,{'e1'})['status'],'OK')

    def test_relay_endpoint_parameter_is_not_accepted_as_official(self):
        for key in ('base_url','endpoint','endpoint_profile','protocol','trust_level','relay'):
            e=entry(); e['parameters'][key]='synthetic-relay'
            with self.subTest(key=key),self.assertRaises(Blocked): validate_entry(e,1000000)

    def test_timeout_keeps_unknown_no_synthetic_success(self):
        obs=parse_response('openai',WireResponse(None,b'',error_code='TIMEOUT'),5,{'e1'})
        self.assertEqual(obs['status'],'TIMEOUT'); self.assertIsNone(obs['output'])
        self.assertIsNone(obs['usage']['input_tokens'])
