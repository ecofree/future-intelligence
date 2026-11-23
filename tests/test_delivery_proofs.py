from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from future_intelligence.delivery import batch
from future_intelligence.delivery.timestamps import make_query, verify_reply, TrustProfile, VerificationError, tlvs, ots_status
from tsa_fixture import fixture_tsa, timestamp


def records():
    return [json.dumps({'record_type':'MANIFEST','experiment_id':str(uuid4()),'task_count':2}),
            json.dumps({'record_type':'TASK','task_id':'a'}), json.dumps({'record_type':'TASK','task_id':'b'})]


class BatchTests(unittest.TestCase):
    def test_roundtrip(self):
        x=batch.seal(str(uuid4()),'FORECAST',records()); self.assertEqual(len(batch.verify(x)),3)
    def test_tampering(self):
        x=batch.seal(str(uuid4()),'FORECAST',records()); x['records'][2]+=' '
        with self.assertRaises(ValueError): batch.verify(x)
    def test_missing_record(self):
        x=batch.seal(str(uuid4()),'FORECAST',records()); x['records'].pop();x['nonces'].pop()
        with self.assertRaises(ValueError): batch.verify(x)
    def test_duplicate_nonce(self):
        with self.assertRaises(ValueError): batch.seal(str(uuid4()),'FORECAST',records(),['a'*64]*3)
    def test_duplicate_keys(self):
        with self.assertRaises(ValueError): batch.strict_json('{"x":1,"x":2}')
    def test_nonfinite(self):
        with self.assertRaises(ValueError): batch.strict_json('{"x":NaN}')
    def test_kind_binding(self):
        x=batch.seal(str(uuid4()),'REGISTRATION',records()); x['kind']='FORECAST'
        with self.assertRaises(ValueError): batch.verify(x)
    def test_registration_omission(self):
        rows=records(); r=batch.seal(str(uuid4()),'REGISTRATION',rows)
        f=batch.seal(str(uuid4()),'FORECAST',rows[:2])
        with self.assertRaises(ValueError): batch.verify_pair(r,f)


class TimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.ca,cls.config=fixture_tsa(Path(cls.tmp.name))
        cls.profile=TrustProfile(hashlib.sha256(cls.ca).hexdigest(),('1.3.6.1.4.1.55555.1',),fixture=True)
        cls.query=make_query('a'*64); cls.reply=timestamp(cls.config,cls.query)
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def verify(self, **changes):
        args=dict(subject_sha256='a'*64,query=self.query,reply=self.reply,ca_pem=self.ca,
                  profile=self.profile,deadline=datetime.now(timezone.utc)+timedelta(hours=1))
        args.update(changes);return verify_reply(**args)
    def test_real_crypto_fixture(self):
        result=self.verify();self.assertEqual(result['status'],'VERIFIED');self.assertTrue(result['fixture'])
        self.assertEqual(result['accuracy_us'],1_000_000)
    def test_binary_imprint_survives_cms_extraction(self):
        subject = '0a0d1a00' * 8
        query = make_query(subject)
        result = self.verify(subject_sha256=subject, query=query, reply=timestamp(self.config, query))
        self.assertEqual(result['subject_sha256'], subject)
    def test_late(self):
        self.assertEqual(self.verify(deadline='2000-01-01T00:00:00Z')['status'],'VERIFIED_LATE')
    def test_wrong_subject(self):
        with self.assertRaises(VerificationError):self.verify(subject_sha256='b'*64)
    def test_nonce_mismatch(self):
        with self.assertRaises(VerificationError):self.verify(query=make_query('a'*64))
    def test_tampered_signature(self):
        b=bytearray(self.reply);b[-10]^=1
        with self.assertRaises(VerificationError):self.verify(reply=bytes(b))
    def test_wrong_trust_pin(self):
        with self.assertRaises(VerificationError):self.verify(profile=TrustProfile('b'*64,self.profile.policy_oids))
    def test_wrong_policy(self):
        with self.assertRaises(VerificationError):self.verify(profile=TrustProfile(self.profile.ca_sha256,('1.2.3',)))
    def test_accuracy_policy(self):
        with self.assertRaises(VerificationError):self.verify(profile=TrustProfile(self.profile.ca_sha256,self.profile.policy_oids,1))
    def test_timezone_required(self):
        with self.assertRaises(VerificationError):self.verify(deadline=datetime(2030,1,1))
    def test_der_length(self):
        for b in (b'\x30\x80',b'\x30\x81\x01\x00',b'\x30\x02\x00'):
            with self.assertRaises(VerificationError):tlvs(b)
    def test_ots_not_confirmed_by_presence(self):
        self.assertEqual(ots_status(b'not-yet-verified')['status'],'PENDING_UNVERIFIED')
        with self.assertRaises(VerificationError):ots_status(b'x','2026-01-01T00:00:00Z')
