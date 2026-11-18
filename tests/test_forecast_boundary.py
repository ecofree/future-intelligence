"""Synthetic boundary cases and a local test TSA, never prospective evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from future_intelligence.delivery.operations import release_question
from future_intelligence.delivery.timestamps import make_query, verify_reply, TrustProfile, VerificationError
from future_intelligence.questions.spec import validate_spec
from future_intelligence.runtime.release import draft_bls_question
from tsa_fixture import fixture_tsa, timestamp
from test_question_spec import example_spec


class ForecastBoundaryTests(unittest.TestCase):
    def spec(self):
        spec = example_spec()
        spec.update(forecast_deadline='2030-12-10T21:30:00+08:00',
                    outcome_not_before='2030-12-10T13:30:00Z',
                    resolution_deadline='2031-01-31T23:59:59+08:00')
        return spec

    def test_exclusive_forecast_and_inclusive_publication_share_an_instant(self):
        result = validate_spec(self.spec())
        self.assertEqual(result['forecast_deadline'], '2030-12-10T13:30:00.000000+00:00')
        self.assertEqual(result['forecast_deadline'], result['outcome_not_before'])

    def test_exact_publication_can_equal_resolution_deadline(self):
        spec = self.spec()
        spec['resolution_deadline'] = spec['outcome_not_before']
        result = validate_spec(spec)
        self.assertEqual(result['outcome_not_before'], result['resolution_deadline'])

    def test_prediction_window_cannot_overlap_outcome_window(self):
        for field, value in (
            ('forecast_deadline', '2030-12-10T13:30:00.000001Z'),
            ('outcome_not_before', '2030-12-10T13:29:59.999999Z'),
            ('resolution_deadline', '2030-12-10T13:29:59.999999Z'),
        ):
            with self.subTest(field=field):
                spec = self.spec()
                spec[field] = value
                with self.assertRaises(ValueError):
                    validate_spec(spec)

    def template(self):
        return dict(series='CUUR0000SA0', year=2030, period='M11', release_family='CPI',
                    forecast_deadline='2030-12-10T21:30:00+08:00',
                    outcome_not_before='2030-12-10T13:30:00Z',
                    resolution_deadline='2031-01-31T23:59:59+08:00',
                    original_timezone='America/New_York',
                    calendar_reference='https://www.bls.gov/schedule/news_release/cpi.htm',
                    bin_edges=['3.0', '3.5', '4.0', '4.5'], unit='percent',
                    review_note='SYNTHETIC boundary test, not a verified release calendar')

    def documents(self):
        return b'SYNTHETIC CALENDAR ONLY', json.dumps({
            'status': 'REQUEST_SUCCEEDED',
            'Results': {'series': [{'seriesID': 'CUUR0000SA0', 'data': []}]},
        }).encode()

    def test_draft_keeps_the_true_publication_instant(self):
        calendar, target = self.documents()
        result = draft_bls_question(self.template(), calendar, target,
                                    now=datetime(2030, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(result['spec']['forecast_deadline'], result['spec']['outcome_not_before'])
        self.assertTrue(result['spec']['outcome_not_before'].endswith('13:30:00.000000+00:00'))
        self.assertEqual(result['status'], 'CANDIDATE_REQUIRES_INDEPENDENT_REVIEW')

    def test_draft_still_requires_now_strictly_before_cutoff(self):
        calendar, target = self.documents()
        cutoff = datetime(2030, 12, 10, 13, 30, tzinfo=timezone.utc)
        for now in (cutoff, cutoff + timedelta(microseconds=1)):
            with self.subTest(now=now), self.assertRaises(ValueError):
                draft_bls_question(self.template(), calendar, target, now=now)
        draft_bls_question(self.template(), calendar, target, now=cutoff - timedelta(microseconds=1))

    def test_legacy_draft_uses_the_same_exclusive_cutoff(self):
        template = self.template()
        del template['release_family']
        template['calendar_sha256'] = hashlib.sha256(b'SYNTHETIC CALENDAR ONLY').hexdigest()
        cutoff = datetime(2030, 12, 10, 13, 30, tzinfo=timezone.utc)
        result = release_question(template, known_target=False, now=cutoff - timedelta(microseconds=1))
        self.assertEqual(result['forecast_deadline'], result['outcome_not_before'])
        with self.assertRaises(ValueError):
            release_question(template, known_target=False, now=cutoff)
        with self.assertRaises(ValueError):
            release_question(template, known_target=True, now=cutoff - timedelta(days=1))


class TimestampBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ca, config = fixture_tsa(Path(cls.tmp.name))
        cls.profile = TrustProfile(hashlib.sha256(cls.ca).hexdigest(), ('1.3.6.1.4.1.55555.1',), fixture=True)
        cls.query = make_query('a' * 64)
        cls.reply = timestamp(config, cls.query)
        cls.baseline = cls.verify(datetime.now(timezone.utc) + timedelta(hours=1))
        cls.upper = datetime.fromisoformat(cls.baseline['upper_time'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def verify(cls, deadline, **kwargs):
        return verify_reply('a' * 64, cls.query, cls.reply, cls.ca, cls.profile, deadline, **kwargs)

    def test_real_signed_proof_upper_bound_at_cutoff_is_late(self):
        self.assertLess(datetime.fromisoformat(self.baseline['gen_time']), self.upper)
        self.assertEqual(self.verify(self.upper)['status'], 'VERIFIED_LATE')

    def test_proof_strictly_before_cutoff_is_eligible(self):
        self.assertEqual(self.verify(self.upper + timedelta(microseconds=1))['status'], 'VERIFIED')

    def test_proof_past_cutoff_is_late(self):
        self.assertEqual(self.verify(self.upper - timedelta(microseconds=1))['status'], 'VERIFIED_LATE')

    def test_future_timestamp_rejection_is_preserved(self):
        gen = datetime.fromisoformat(self.baseline['gen_time'])
        with self.assertRaises(VerificationError):
            self.verify(self.upper + timedelta(hours=1), now=gen - timedelta(seconds=6))
