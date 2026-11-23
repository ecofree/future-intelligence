"""Local synthetic TSA/CRL cases only; none are production time evidence."""
import base64
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from future_intelligence.delivery.timestamps import (
    CRL_POLICY, CRLTrustProfile, MAX_PROOF_BYTES, TrustProfile, VerificationError,
    _aped_window, _crl_time, _crl_times, make_query, trust_profile_from_dict, verify_reply,
)
from tsa_fixture import fixture_crl, fixture_intermediate_tsa, fixture_tsa, timestamp


UTC = timezone.utc
V1_FIELDS = {'protocol', 'status', 'subject_sha256', 'gen_time', 'upper_time', 'accuracy_us',
             'policy_oid', 'ca_sha256', 'reply_sha256', 'fixture', 'verifier', 'revocation_status'}
V2_FIELDS = V1_FIELDS | {'crl_sha256', 'crl_base64', 'crl_valid_from', 'crl_valid_until'}


def der(tag, value):
    length = len(value)
    encoded = bytes([length]) if length < 128 else length.to_bytes((length.bit_length() + 7) // 8, 'big')
    if length >= 128:
        encoded = bytes([128 | len(encoded)]) + encoded
    return bytes([tag]) + encoded + value


def crl_structure(last_update, next_update=None):
    """Unsigned syntax fixture exclusively for the date reader's negative cases."""
    fields = der(48, b'') + der(48, b'') + der(*last_update)
    if next_update is not None:
        fields += der(*next_update)
    return der(48, der(48, fields) + der(48, b'') + der(3, b'\x00'))


def pem_crl(value):
    body = base64.b64encode(value)
    return (b'-----BEGIN X509 CRL-----\n' +
            b'\n'.join(body[i:i+64] for i in range(0, len(body), 64)) +
            b'\n-----END X509 CRL-----\n')


class TrustProfileTests(unittest.TestCase):
    def setUp(self):
        self.old = TrustProfile('a' * 64, ('1.2.3',), fixture=True)

    def test_deployed_aped_profile_is_fixed(self):
        value = json.loads((Path(__file__).resolve().parents[1]/'deploy'/'aped-trust-profile.json').read_text())
        trust_profile_from_dict(value)
        for key, replacement in [('fixture',True),('ca_sha256','a'*64),('max_accuracy_us',2000000),
                                 ('missing_accuracy_us',1000000),('policy_oids',['1.2.3']),('max_crl_age_seconds',86400)]:
            with self.subTest(key=key), self.assertRaises(VerificationError):
                trust_profile_from_dict({**value,key:replacement})

    def test_v1_dictionary_shape_is_unchanged(self):
        self.assertEqual(set(self.old.__dict__),
                         {'ca_sha256', 'policy_oids', 'max_accuracy_us', 'missing_accuracy_us', 'fixture'})
        result = trust_profile_from_dict({**self.old.__dict__, 'policy_oids': ['1.2.3']})
        self.assertIs(type(result), TrustProfile)
        self.assertEqual(result, self.old)

    def test_v2_requires_both_explicit_keyword_fields(self):
        for kwargs in ({}, {'revocation_policy': CRL_POLICY}, {'max_crl_age_seconds': 60}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                CRLTrustProfile(**self.old.__dict__, **kwargs)
        with self.assertRaises(TypeError):
            CRLTrustProfile('a' * 64, ('1.2.3',), 5_000_000, None, True, CRL_POLICY, 60)

    def test_v2_roundtrip_is_frozen_and_exactly_seven_fields(self):
        value = {**self.old.__dict__, 'revocation_policy': CRL_POLICY, 'max_crl_age_seconds': 60}
        result = trust_profile_from_dict(value)
        self.assertIs(type(result), CRLTrustProfile)
        self.assertEqual(result.__dict__, value)
        with self.assertRaises(FrozenInstanceError):
            result.max_crl_age_seconds = 0

    def test_profiles_reject_unknown_missing_or_invalid_policy_fields(self):
        invalid = [None, {}, {**self.old.__dict__, 'extra': False},
                   {**self.old.__dict__, 'revocation_policy': CRL_POLICY},
                   {**self.old.__dict__, 'policy_oids': '1.2.3'},
                   {**self.old.__dict__, 'policy_oids': [None]}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(VerificationError):
                trust_profile_from_dict(value)
        for age in (True, False, 0, -1, 31_536_001, 1.5, '60', None):
            with self.subTest(age=age), self.assertRaises(VerificationError):
                trust_profile_from_dict({**self.old.__dict__, 'revocation_policy': CRL_POLICY,
                                         'max_crl_age_seconds': age})
        for policy in ('', 'off', 'offline-leaf-only', None):
            with self.subTest(policy=policy), self.assertRaises(VerificationError):
                trust_profile_from_dict({**self.old.__dict__, 'revocation_policy': policy,
                                         'max_crl_age_seconds': 60})


class CRLTimeParserTests(unittest.TestCase):
    def test_utc_time_uses_rfc_5280_century_boundary(self):
        self.assertEqual(_crl_time((23, b'491231235959Z')).year, 2049)
        self.assertEqual(_crl_time((23, b'500101000000Z')).year, 1950)
        self.assertEqual(_crl_time((24, b'20500101000000Z')).year, 2050)

    def test_missing_next_update_is_rejected_explicitly(self):
        with self.assertRaises(VerificationError):
            _crl_times(crl_structure((23, b'261119010000Z')))

    def test_noncanonical_or_invalid_dates_are_rejected(self):
        for field in ((23, b'2611190100Z'), (23, b'261119010000+0000'),
                      (23, b'261119010000.1Z'), (23, b'261332010000Z'),
                      (24, b'00000101000000Z'), (24, b'20261119010000.0Z'), (4, b'261119010000Z')):
            with self.subTest(field=field), self.assertRaises(VerificationError):
                _crl_time(field)

    def test_empty_or_reversed_validity_window_is_rejected(self):
        for next_update in (b'261119010000Z', b'261118010000Z'):
            with self.subTest(next_update=next_update), self.assertRaises(VerificationError):
                _crl_times(crl_structure((23, b'261119010000Z'), (23, next_update)))


class TimestampRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tmp.name)
        cls.ca, cls.config = fixture_tsa(cls.directory)
        cls.query = make_query('a' * 64)
        cls.reply = timestamp(cls.config, cls.query)
        cls.now = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=5)
        cls.last_update = cls.now - timedelta(minutes=5)
        cls.next_update = cls.now + timedelta(days=1)
        cls.profile = CRLTrustProfile(hashlib.sha256(cls.ca).hexdigest(), ('1.3.6.1.4.1.55555.1',),
                                     fixture=True, revocation_policy=CRL_POLICY, max_crl_age_seconds=3600)
        cls.crl = cls.make_crl()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def make_crl(cls, **kwargs):
        values = dict(last_update=cls.last_update, next_update=cls.next_update)
        values.update(kwargs)
        return fixture_crl(cls.directory, **values)

    @classmethod
    def verify(cls, **kwargs):
        values = dict(subject_sha256='a' * 64, query=cls.query, reply=cls.reply, ca_pem=cls.ca,
                      profile=cls.profile, deadline=cls.now + timedelta(hours=1),
                      now=cls.now, crl_bundle=cls.crl)
        values.update(kwargs)
        return verify_reply(**values)

    def test_real_full_chain_crl_verification_has_exact_stable_report(self):
        first = self.verify()
        second = self.verify(now=self.now + timedelta(seconds=1))
        self.assertEqual(first, second)
        self.assertEqual(set(first), V2_FIELDS)
        self.assertEqual(first['protocol'], 'fi.rfc3161-verification.v2')
        self.assertEqual(first['verifier'], 'openssl-ts+fi-tstinfo-crl-v2')
        self.assertEqual(first['revocation_status'], 'GOOD')
        self.assertEqual(first['status'], 'VERIFIED')
        self.assertTrue(first['fixture'])
        self.assertEqual(first['ca_sha256'], hashlib.sha256(self.ca).hexdigest())
        self.assertEqual(first['crl_sha256'], hashlib.sha256(self.crl).hexdigest())
        self.assertEqual(base64.b64decode(first['crl_base64'], validate=True), self.crl)
        self.assertEqual(first['crl_valid_from'], self.last_update.isoformat().replace('+00:00', 'Z'))
        self.assertEqual(first['crl_valid_until'],
                         (self.last_update + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'))

    def test_v1_still_has_original_report_without_crl(self):
        profile = TrustProfile(self.profile.ca_sha256, self.profile.policy_oids, fixture=True)
        result = self.verify(profile=profile, crl_bundle=None)
        self.assertEqual(set(result), V1_FIELDS)
        self.assertEqual(result['protocol'], 'fi.rfc3161-verification.v1')
        self.assertEqual(result['revocation_status'], 'NOT_CHECKED')
        with self.assertRaises(VerificationError):
            self.verify(profile=profile)

    def test_no_crl_fails_even_though_the_same_token_passes_v1(self):
        for value in (None, b'', b' \r\n\t'):
            with self.subTest(value=value), self.assertRaises(VerificationError):
                self.verify(crl_bundle=value)

    def test_revoked_signer_is_rejected(self):
        revoked = self.make_crl(revoked=(self.directory/'tsa.pem',))
        with self.assertRaises(VerificationError):
            self.verify(crl_bundle=revoked)

    def test_expired_or_future_crl_is_rejected(self):
        for dates in (dict(last_update=self.now - timedelta(minutes=10), next_update=self.now - timedelta(seconds=1)),
                      dict(last_update=self.now + timedelta(seconds=1), next_update=self.next_update)):
            with self.subTest(dates=dates), self.assertRaises(VerificationError):
                self.verify(crl_bundle=self.make_crl(**dates))

    def test_maximum_age_is_enforced_even_before_next_update(self):
        with self.assertRaises(VerificationError):
            self.verify(profile=replace(self.profile, max_crl_age_seconds=300))
        self.assertEqual(self.verify(profile=replace(self.profile, max_crl_age_seconds=301))['status'], 'VERIFIED')

    def test_crl_window_includes_start_and_excludes_end(self):
        crl = self.make_crl(last_update=self.now, next_update=self.now + timedelta(seconds=30))
        self.assertEqual(self.verify(crl_bundle=crl)['status'], 'VERIFIED')
        with self.assertRaises(VerificationError):
            self.verify(crl_bundle=crl, now=self.now + timedelta(seconds=30))

    def test_corrupt_crl_signature_is_rejected(self):
        value = bytearray(base64.b64decode(b''.join(self.crl.splitlines()[1:-1]), validate=True))
        value[-1] ^= 1
        with self.assertRaises(VerificationError):
            self.verify(crl_bundle=pem_crl(bytes(value)))

    def test_wrong_crl_issuer_or_wrong_pinned_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            other_ca, _ = fixture_tsa(directory)
            wrong_crl = fixture_crl(directory, last_update=self.last_update, next_update=self.next_update)
            with self.assertRaises(VerificationError):
                self.verify(crl_bundle=wrong_crl)
            with self.assertRaises(VerificationError):
                self.verify(ca_pem=other_ca)
            with self.assertRaises(VerificationError):
                self.verify(ca_pem=other_ca, profile=replace(self.profile,
                            ca_sha256=hashlib.sha256(other_ca).hexdigest()), crl_bundle=wrong_crl)

    def test_extra_certificates_junk_and_excess_blocks_are_rejected(self):
        for value in (self.crl + self.ca, self.ca + self.crl, b'comment\n' + self.crl,
                      self.crl + b'garbage', self.crl * 9, b'x' * (MAX_PROOF_BYTES + 1),
                      self.crl.rstrip() + self.crl,
                      self.crl.replace(b'BEGIN X509 CRL', b'BEGIN CERTIFICATE')):
            with self.subTest(size=len(value), prefix=value[:30]), self.assertRaises(VerificationError):
                self.verify(crl_bundle=value)

    def test_invalid_base64_and_certificate_wrapped_as_crl_are_rejected(self):
        invalid = (b'-----BEGIN X509 CRL-----\n!!!!\n-----END X509 CRL-----\n',
                   b'-----BEGIN X509 CRL-----\nA===\n-----END X509 CRL-----\n',
                   self.ca.replace(b'CERTIFICATE', b'X509 CRL'))
        for value in invalid:
            with self.subTest(prefix=value[:50]), self.assertRaises(VerificationError):
                self.verify(crl_bundle=value)

    def test_crl_cannot_be_hidden_in_the_pinned_ca_bundle(self):
        combined = self.ca + self.crl
        with self.assertRaises(VerificationError):
            self.verify(ca_pem=combined, profile=replace(self.profile, ca_sha256=hashlib.sha256(combined).hexdigest()))

    def test_production_shaped_profile_cannot_override_current_time(self):
        profile = replace(self.profile, fixture=False)
        with self.assertRaisesRegex(VerificationError, 'cannot override'):
            self.verify(profile=profile)

    def test_current_time_verification_works_without_synthetic_clock(self):
        self.assertEqual(self.verify(now=None)['revocation_status'], 'GOOD')

    def test_fresh_crl_cannot_make_expired_certificates_valid(self):
        later = self.now + timedelta(days=3)
        fresh = self.make_crl(last_update=later - timedelta(minutes=1), next_update=later + timedelta(hours=1))
        with self.assertRaises(VerificationError):
            self.verify(crl_bundle=fresh, now=later, deadline=later + timedelta(hours=1))

    def test_strict_forecast_cutoff_is_preserved(self):
        upper = datetime.fromisoformat(self.verify()['upper_time'])
        self.assertEqual(self.verify(deadline=upper)['status'], 'VERIFIED_LATE')
        self.assertEqual(self.verify(deadline=upper - timedelta(microseconds=1))['status'], 'VERIFIED_LATE')
        self.assertEqual(self.verify(deadline=upper + timedelta(microseconds=1))['status'], 'VERIFIED')


class IntermediateRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tmp.name)
        cls.ca, config = fixture_intermediate_tsa(cls.directory)
        cls.query = make_query('b' * 64)
        cls.reply = timestamp(config, cls.query)
        cls.profile = CRLTrustProfile(hashlib.sha256(cls.ca).hexdigest(), ('1.3.6.1.4.1.55555.1',),
                                     fixture=True, revocation_policy=CRL_POLICY, max_crl_age_seconds=3600)
        now = datetime.now(UTC).replace(microsecond=0)
        cls.valid_from = now - timedelta(minutes=1)
        cls.valid_until = now + timedelta(minutes=30)
        cls.root_crl = fixture_crl(cls.directory, last_update=now - timedelta(minutes=5),
                                   next_update=cls.valid_until)
        cls.leaf_crl = fixture_crl(cls.directory, issuer='intermediate', last_update=cls.valid_from,
                                   next_update=now + timedelta(hours=1))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_aped_rechecks_both_issuers_with_different_expiry_windows(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        now = datetime.now(UTC).replace(microsecond=0)
        issuer = (self.directory/'intermediate.pem').read_bytes()
        signer = (self.directory/'tsa.pem').read_bytes()
        fp = x509.load_pem_x509_certificate(issuer).fingerprint(hashes.SHA256()).hex()
        root_crl = fixture_crl(self.directory,last_update=now-timedelta(days=90),next_update=now+timedelta(days=3))
        def daily(hours):
            return fixture_crl(self.directory,issuer='intermediate',last_update=now-timedelta(hours=hours),
                               next_update=now+timedelta(days=2))
        fresh, stale = daily(23), daily(25)
        with self.assertRaises(VerificationError):
            _aped_window(self.ca,issuer,signer,root_crl+fresh,now)
        with patch('future_intelligence.delivery.timestamps.APED_ISSUER_DER_SHA256',fp), \
             patch('future_intelligence.delivery.timestamps.APED_REVIEW_UNTIL',now+timedelta(days=10)):
            self.assertEqual(_aped_window(self.ca,issuer,signer,root_crl+fresh,now)[1],now+timedelta(hours=1))
            for bundle in (root_crl+stale, root_crl+root_crl, fresh+fresh, root_crl+fresh+fresh):
                with self.assertRaises(VerificationError):
                    _aped_window(self.ca,issuer,signer,bundle,now)

    def verify(self, bundle):
        return verify_reply('b' * 64, self.query, self.reply, self.ca, self.profile,
                            datetime.now(UTC) + timedelta(hours=1), crl_bundle=bundle)

    def test_full_three_level_chain_with_both_issuer_crls_passes(self):
        report = self.verify(self.root_crl + self.leaf_crl)
        self.assertEqual(report['revocation_status'], 'GOOD')
        self.assertEqual(report['crl_valid_from'], self.valid_from.isoformat().replace('+00:00', 'Z'))
        self.assertEqual(report['crl_valid_until'], self.valid_until.isoformat().replace('+00:00', 'Z'))

    def test_clean_leaf_crl_cannot_hide_revoked_intermediate(self):
        revoked = fixture_crl(self.directory, revoked=(self.directory/'intermediate.pem',))
        with self.assertRaises(VerificationError):
            self.verify(revoked + self.leaf_crl)

    def test_clean_leaf_crl_does_not_substitute_for_missing_upper_crl(self):
        with self.assertRaises(VerificationError):
            self.verify(self.leaf_crl)

    def test_root_crl_does_not_substitute_for_missing_leaf_issuer_crl(self):
        with self.assertRaises(VerificationError):
            self.verify(self.root_crl)
