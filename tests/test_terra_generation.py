"""Explicit official Terra request contract; all payloads synthetic, no API calls."""
import copy
import unittest
import test_forecast_contract as fixture
from future_intelligence.providers.live import (
    Blocked, PROMPT_SHA256, build_request, encode, generation_wire,
    strict_json, terra_generation_profile, validate_entry,
)


def entry():
    cfg = fixture.entry()
    cfg['model_id'] = 'gpt-6.1-terra'
    cfg['parameters']['generation'] = terra_generation_profile()
    return cfg


class TerraGenerationTests(unittest.TestCase):
    def test_binary_and_five_category_exact_wire(self):
        for edges, count in ((None, 2), (['3.0','3.5','4.0','4.5'], 5)):
            cfg = entry()
            validated = validate_entry(cfg, 1000000)
            body = strict_json(build_request(validated, fixture.context(edges)))
            self.assertEqual(body['model'], 'gpt-6.1-terra')
            self.assertEqual(body['reasoning'], {'effort':'medium','mode':'standard'})
            self.assertEqual(body['service_tier'], 'default')
            self.assertEqual(set(body), {'model','instructions','input','max_output_tokens',
                             'tools','store','stream','text','reasoning','service_tier'})
            schema = body['text']['format']['schema']['properties']['probabilities_ppm']
            self.assertEqual((schema['minItems'],schema['maxItems']), (count,count))
            self.assertEqual(body['tools'], [])
            self.assertIs(body['store'], False)
            self.assertIs(body['stream'], False)

    def test_omissions_are_metadata_never_wire(self):
        cfg=entry(); body=strict_json(build_request(cfg, fixture.context()))
        for key in ('temperature','top_p','seed'):
            self.assertNotIn(key, body)
            self.assertTrue(cfg['parameters']['generation'][key].startswith('OMIT_'))

    def test_profile_is_fresh_and_input_not_mutated(self):
        cfg=entry(); before=copy.deepcopy(cfg)
        profile=terra_generation_profile(); profile['reasoning']['effort']='high'
        self.assertEqual(terra_generation_profile()['reasoning']['effort'],'medium')
        build_request(cfg,fixture.context()); validate_entry(cfg,1000000)
        self.assertEqual(cfg,before)

    def test_legacy_request_bytes_unchanged(self):
        for legacy in (False,True):
            cfg=fixture.entry(legacy=legacy); cfg['model_id']='gpt-6.1-terra'
            ctx=fixture.context(legacy=legacy)
            old=strict_json(build_request(cfg,ctx))
            self.assertNotIn('reasoning',old);self.assertNotIn('service_tier',old)
            self.assertEqual(generation_wire(cfg),{})
            if not legacy:
                new=copy.deepcopy(cfg);new['parameters']['generation']=terra_generation_profile()
                changed=strict_json(build_request(new,ctx))
                changed.pop('reasoning');changed.pop('service_tier')
                self.assertEqual(encode(changed),encode(old))

    def test_other_provider_model_or_prompt_not_silently_accepted(self):
        for key,value in (('provider','anthropic'),('provider','google'),
                          ('provider','openai_compatible'),('model_id','other-model'),
                          ('prompt_sha256',PROMPT_SHA256)):
            cfg=entry();cfg[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(Blocked):
                generation_wire(cfg)

    def test_changed_missing_or_unknown_generation_fields_rejected(self):
        for key in terra_generation_profile():
            cfg=entry();del cfg['parameters']['generation'][key]
            with self.subTest(key=key),self.assertRaises(Blocked):validate_entry(cfg,1000000)
        for value in (None,{},[],{'effort':'medium'},'medium'):
            cfg=entry();cfg['parameters']['generation']=value
            with self.subTest(value=value),self.assertRaises(Blocked):validate_entry(cfg,1000000)
        for key,value in (('reasoning',{'effort':'high','mode':'standard'}),
                          ('reasoning',{'effort':'medium','mode':'pro'}),
                          ('service_tier','priority'),('seed',42),('temperature',0),('unknown',True)):
            cfg=entry();cfg['parameters']['generation'][key]=value
            with self.subTest(key=key),self.assertRaises(Blocked):validate_entry(cfg,1000000)

    def test_no_direct_parameter_or_endpoint_passthrough(self):
        for key,value in (('temperature',0),('top_p',1),('seed',42),('reasoning',{}),
                          ('base_url','https://example.invalid'),('service_tier','priority')):
            cfg=entry();cfg['parameters'][key]=value
            with self.subTest(key=key),self.assertRaises(Blocked):validate_entry(cfg,1000000)

    def test_budget_guard_is_not_relaxed(self):
        with self.assertRaisesRegex(Blocked,'RESERVATION_TOO_SMALL'):
            validate_entry(entry(),1)
