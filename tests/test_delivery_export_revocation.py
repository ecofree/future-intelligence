"""Independent export verification with ephemeral TEST ONLY keys and tokens."""
import base64
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4

from future_intelligence.delivery import batch
from future_intelligence.delivery.exports import verify_export
from future_intelligence.delivery.timestamps import (
    CRLTrustProfile, TrustProfile, VerificationError, make_query, verify_reply, utc,
)
from tsa_fixture import fixture_crl, fixture_tsa, timestamp


class ExportRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.directory=Path(cls.tmp.name)
        cls.ca,cls.config=fixture_tsa(cls.directory)
        cls.crl=fixture_crl(cls.directory)
        cls.legacy=TrustProfile(hashlib.sha256(cls.ca).hexdigest(),('1.3.6.1.4.1.55555.1',))
        # fixture=False exercises the production-shaped gate with LOCAL TEST keys;
        # it is never an approval to trust this CA outside the isolated test.
        cls.profile=CRLTrustProfile(**asdict(cls.legacy),
            revocation_policy='offline-full-chain-crl-v1',max_crl_age_seconds=3600)
        cls.old=cls.bundle(cls.legacy)
        cls.current=cls.bundle(cls.profile,cls.crl)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def bundle(cls,profile,crl=None):
        experiment,version,question,task=[str(uuid4()) for _ in range(4)]
        deadline=datetime.now(timezone.utc)+timedelta(hours=1)
        snapshot=json.dumps({'id':version,'question_id':question,
                             'spec':{'question_type':'binary','bin_edges':[]}})
        digest=hashlib.sha256(snapshot.encode()).hexdigest()
        manifest=json.dumps({'protocol':'fi.experiment-manifest.v1',
            'questions':[{'version_id':version,'snapshot_sha256':digest}],
            'entries':[{'entry_key':'test-only'}],
            'spec':{'run_kind':'LIVE','suite':'A','forecast_deadline':deadline.isoformat()}})
        header={'record_type':'MANIFEST','experiment_id':experiment,'run_kind':'LIVE','suite':'A',
            'task_count':1,'experiment_manifest':manifest,'evidence_manifest':None,'analysis_plan':{},
            'trust_profile':asdict(profile),
            'questions':[{'version_id':version,'snapshot_text':snapshot,'snapshot_sha256':digest}]}
        identity={'record_type':'TASK','task_id':task,'question_version_id':version,
                  'entry_key':'test-only','entry_config_sha256':'a'*64}
        registration=batch.seal(str(uuid4()),'REGISTRATION',[json.dumps(header),json.dumps(identity)])
        def anchor(sealed):
            query=make_query(sealed['subject_sha256']);reply=timestamp(cls.config,query)
            report=verify_reply(sealed['subject_sha256'],query,reply,cls.ca,profile,deadline,crl_bundle=crl)
            return {'id':str(uuid4()),'batch_id':sealed['batch_id'],'protocol':'RFC3161',
                'query_b64':base64.b64encode(query).decode(),'reply_b64':base64.b64encode(reply).decode(),
                'claimed_verification':report}
        registered=anchor(registration)
        upper=utc(registered['claimed_verification']['upper_time'])
        time.sleep(max(0,(upper-datetime.now(timezone.utc)).total_seconds())+.02)
        final_header={**header,'first_attempt_at':datetime.now(timezone.utc).isoformat()}
        final_task={**identity,'submission':{'task_id':task,'run_kind':'LIVE',
                                            'probabilities_ppm':[400000,600000]}}
        forecast=batch.seal(str(uuid4()),'FORECAST',[json.dumps(final_header),json.dumps(final_task)])
        return {'protocol':'fi.audit-export.v1','registration':registration,'forecast':forecast,
                'anchors':[registered,anchor(forecast)],'outcomes':[]}

    def test_legacy_crypto_is_retained_but_not_production_eligible(self):
        result=verify_export(self.old,self.ca,self.legacy)
        self.assertTrue(result['precise_time_verified'])
        self.assertTrue(result['registration_precedes_first_attempt'])
        self.assertFalse(result['revocation_checked'])
        self.assertFalse(result['time_eligible_live'])
        self.assertTrue(all(a['revocation_status']=='NOT_CHECKED' for a in result['anchors']))

    def test_claimed_good_does_not_promote_legacy_proof(self):
        value=deepcopy(self.old)
        for anchor in value['anchors']:
            anchor['claimed_verification']={'status':'VERIFIED','revocation_status':'GOOD',
                                            'crl_base64':base64.b64encode(self.crl).decode()}
        self.assertFalse(verify_export(value,self.ca,self.legacy)['time_eligible_live'])

    def test_retained_crl_is_reverified_with_real_token(self):
        value=deepcopy(self.current)
        for anchor in value['anchors']:
            anchor['claimed_verification']['status']='UNTRUSTED_CLAIM'
            anchor['claimed_verification']['crl_valid_until']='1900-01-01T00:00:00Z'
        result=verify_export(value,self.ca,self.profile)
        self.assertTrue(result['time_eligible_live'])
        self.assertTrue(result['revocation_checked'])
        self.assertEqual(result['verification_policy'],'CURRENT_TIME_REVERIFICATION')
        self.assertEqual(result['dispatch_admission'],'REQUIRES_ORIGINAL_DATABASE_AUDIT')
        self.assertTrue(all(a['revocation_status']=='GOOD' for a in result['anchors']))

    def test_missing_crl_cannot_fall_back_to_signature_only(self):
        value=deepcopy(self.current)
        value['anchors'][0]['claimed_verification'].pop('crl_base64')
        result=verify_export(value,self.ca,self.profile)
        self.assertFalse(result['time_eligible_live'])
        self.assertEqual(result['anchors'][0]['status'],'INVALID_PROOF')

    def test_retained_crl_digest_or_base64_tampering_fails(self):
        for change in ({'crl_sha256':'0'*64},{'crl_base64':'%%%%'}):
            with self.subTest(change=change):
                value=deepcopy(self.current)
                value['anchors'][0]['claimed_verification'].update(change)
                self.assertFalse(verify_export(value,self.ca,self.profile)['time_eligible_live'])

    def test_expired_material_is_not_checked_at_a_bundle_supplied_old_time(self):
        old=fixture_crl(self.directory,last_update=datetime.now(timezone.utc)-timedelta(days=2),
                        next_update=datetime.now(timezone.utc)-timedelta(days=1))
        value=deepcopy(self.current)
        for anchor in value['anchors']:
            anchor['claimed_verification'].update(crl_base64=base64.b64encode(old).decode(),
                crl_sha256=hashlib.sha256(old).hexdigest(),verified_at='2000-01-01T00:00:00Z')
        result=verify_export(value,self.ca,self.profile)
        self.assertFalse(result['time_eligible_live'])
        self.assertTrue(all(a['status']=='INVALID_PROOF' for a in result['anchors']))

    def test_current_revoked_material_overrides_retained_clean_claims(self):
        revoked=fixture_crl(self.directory,revoked=[self.directory/'tsa.pem'])
        result=verify_export(self.current,self.ca,self.profile,crl_bundle=revoked)
        self.assertFalse(result['time_eligible_live'])
        self.assertTrue(all(a['status']=='INVALID_PROOF' for a in result['anchors']))

    def test_external_current_crl_does_not_mutate_old_export(self):
        before=deepcopy(self.current)
        result=verify_export(self.current,self.ca,self.profile,crl_bundle=self.crl)
        self.assertTrue(result['time_eligible_live'])
        self.assertEqual(self.current,before)

    def test_legacy_profile_cannot_silently_import_crl_policy(self):
        with self.assertRaises(VerificationError):
            verify_export(self.old,self.ca,self.legacy,crl_bundle=self.crl)

    def test_untrusted_export_cannot_choose_its_own_profile(self):
        with self.assertRaises(VerificationError):verify_export(self.current,self.ca,self.legacy)
