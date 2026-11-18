"""Real PostgreSQL, synthetic events only. No live provider or production TSA.

The SQL attestation cases use explicitly synthetic reports to exercise the
isolated verifier's database contract. Real signature verification at the same
boundary is tested with the ephemeral local CA in tests/test_forecast_boundary.py.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
from importlib.resources import files
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

import test_delivery_pipeline as fixture
from test_question_spec import example_spec
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.tasks.repository import TaskRepository


class ForecastBoundaryIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.DeliveryPipelineTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DeliveryPipelineTests.tearDownClass.__func__)

    def scope(self, database=None):
        dsns = self.dsns if database is None else {
            role: make_conninfo(dsn, dbname=database) for role, dsn in self.dsns.items()
        }
        return dict(dsns=dsns, d={role: DeliveryRepository(dsn) for role, dsn in dsns.items()},
                    t={role: TaskRepository(dsn) for role, dsn in dsns.items()})

    def question(self, cutoff, *, outcome=None, resolution=None, freeze=False, scope=None):
        scope = scope or self.scope()
        now = datetime.now(timezone.utc)
        source = scope['d']['source'].one('SELECT fi.register_source(%s,%s)',
            ('SYNTHETIC boundary fixture', 'https://example.invalid/synthetic-boundary'))
        permission = scope['d']['source'].one('SELECT fi.grant_source_permission(%s,%s,%s,%s,%s)',
            (source, fixture.USES, now - timedelta(days=1), now + timedelta(days=3),
             'SYNTHETIC permission for an isolated regression; no actual publication'))
        spec = example_spec()
        spec.update(forecast_deadline=cutoff.isoformat(),
                    outcome_not_before=(outcome or cutoff).isoformat(),
                    resolution_deadline=(resolution or cutoff + timedelta(hours=1)).isoformat(),
                    event_cluster='synthetic-boundary-' + uuid4().hex)
        author = QuestionRepository(scope['dsns']['author'])
        version = author.create_version('boundary-' + uuid4().hex, spec, [str(permission)])
        if freeze:
            QuestionRepository(scope['dsns']['reviewer']).review(version, 'SYNTHETIC independent boundary review')
            author.freeze(version)
        return dict(scope=scope, spec=spec, version=version, permission=str(permission))

    def experiment(self, cutoff, *, outcome=None, scope=None):
        item = self.question(cutoff, outcome=outcome, freeze=True, scope=scope)
        entry = dict(entry_key='model-0', provider='fixture', model_id='fixture-boundary',
                     prompt_sha256='a' * 64, parameters={})
        spec = dict(protocol='fi.experiment.v1', suite='A', run_kind='FIXTURE',
                    forecast_deadline=cutoff.isoformat(), budget_microusd=10000,
                    attempt_cap_microusd=100, max_attempts=1, lease_seconds=3600,
                    failure_policy='REPORT_MISSING', baseline_policy='UNIFORM_CATEGORIES_V1',
                    disclosure_policy='COMPLETE_BATCH_AFTER_RESOLUTION')
        repo = item['scope']['t']['operator']
        item['experiment'] = repo.register('boundary-exp-' + uuid4().hex, [item['version']], [entry], spec)
        repo.bind_worker(item['experiment'], 'model-0', self.logins['worker'])
        return item

    def registration(self, item):
        d = item['scope']['d']['operator']
        # A wide, explicit synthetic accuracy limit lets SQL compare an upper
        # bound at a future cutoff while its asserted gen_time stays in the past.
        profile = {**self.profile_json, 'max_accuracy_us': 60000000}
        item['profile'] = profile
        bid = d.capture(item['experiment'], 'REGISTRATION', {**fixture.PLAN, 'comparisons': []}, profile)
        item['registration'] = d.seal(bid)
        return item['registration']

    def synthetic_report(self, item, upper, status):
        d = item['scope']['d']['operator']
        batch = item.get('registration') or self.registration(item)
        rid, _ = d.new_anchor(batch['batch_id'])
        raw = b'SYNTHETIC SQL ATTESTATION FIXTURE - NOT A TIMESTAMP PROOF'
        d.receive(rid, raw)
        report = dict(protocol='fi.rfc3161-verification.v1', status=status,
                      subject_sha256=batch['subject_sha256'],
                      gen_time=(upper - timedelta(seconds=60)).isoformat(),
                      upper_time=upper.isoformat(), accuracy_us=60000000,
                      policy_oid=item['profile']['policy_oids'][0], ca_sha256=item['profile']['ca_sha256'],
                      reply_sha256=hashlib.sha256(raw).hexdigest(), fixture=True,
                      verifier='openssl-ts+fi-tstinfo-v1', revocation_status='NOT_CHECKED')
        return rid, report

    def attest(self, item, rid, report):
        item['scope']['d']['verifier'].one('SELECT fi.attest_anchor(%s,%s)', (rid, Jsonb(report)))

    def evidence(self, item, published, key):
        return item['scope']['d']['resolver'].add_evidence(
            item['version'], item['permission'], key, published.isoformat(), 0, '1',
            hashlib.sha256(b'SYNTHETIC original release').hexdigest(),
            'SYNTHETIC boundary fixture, explicitly not BLS evidence')

    def test_equal_window_is_stored_without_timestamp_adjustment(self):
        cutoff = datetime.now(timezone.utc) + timedelta(days=1)
        item = self.question(cutoff, resolution=cutoff)
        row = item['scope']['d']['author'].one(
            'SELECT to_jsonb(v) FROM fi.question_catalog v WHERE id=%s', (item['version'],))
        self.assertEqual(row['forecast_deadline'], row['outcome_not_before'])
        self.assertEqual(row['outcome_not_before'], row['resolution_deadline'])
        self.assertEqual(hashlib.sha256(row['snapshot_text'].encode()).hexdigest(), row['snapshot_sha256'])
        bad = {**item['spec'], 'forecast_deadline': (cutoff + timedelta(microseconds=1)).isoformat()}
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            with psycopg.connect(fixture.DSN) as c:
                c.execute('SELECT fi.validate_spec(%s)', (Jsonb(bad),))

    def test_publication_window_contains_both_exact_endpoints_only(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=10)
        end = start + timedelta(seconds=1)
        item = self.question(start, resolution=end)
        self.evidence(item, start, 'exact-first-publication')
        self.evidence(item, end, 'exact-resolution-cutoff')
        for key, published in (
            ('before', start - timedelta(microseconds=1)),
            ('after', end + timedelta(microseconds=1)),
        ):
            with self.subTest(key=key), self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                self.evidence(item, published, key)

    def test_future_publication_remains_rejected(self):
        now = datetime.now(timezone.utc)
        item = self.question(now - timedelta(seconds=1), resolution=now + timedelta(hours=1))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.evidence(item, now + timedelta(minutes=1), 'not-yet-published')

    def test_when_outcomes_open_late_predictions_are_rejected(self):
        cutoff = datetime.now(timezone.utc) + timedelta(seconds=4)
        item = self.experiment(cutoff)
        worker = item['scope']['t']['worker']
        lease = worker.claim(item['experiment'], 'FIXTURE')
        self.assertIsNotNone(lease)
        worker.start_call(lease)
        time.sleep(max(0, (cutoff - datetime.now(timezone.utc)).total_seconds()) + .02)
        self.assertIsNone(worker.claim(item['experiment'], 'FIXTURE'))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            worker.succeed(lease, [500000, 500000], 'b' * 64, 10)
        self.assertEqual(item['scope']['d']['operator'].one(
            'SELECT count(*) FROM fi.task_submissions WHERE task_id=%s', (lease['task_id'],)), 0)
        self.evidence(item, cutoff, 'true-release-time-with-no-added-microsecond')
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.registration(item)

    def test_sql_attestation_at_cutoff_is_late_not_eligible(self):
        cutoff = datetime.now(timezone.utc) + timedelta(seconds=30)
        item = self.experiment(cutoff)
        rid, report = self.synthetic_report(item, cutoff, 'VERIFIED')
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.attest(item, rid, report)
        report['status'] = 'VERIFIED_LATE'
        self.attest(item, rid, report)
        self.assertIsNone(item['scope']['d']['operator'].one(
            'SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s',
            (item['registration']['batch_id'],)))
        before_rid, before = self.synthetic_report(item, cutoff - timedelta(microseconds=1), 'VERIFIED')
        self.attest(item, before_rid, before)
        self.assertEqual(item['scope']['d']['operator'].one(
            'SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s',
            (item['registration']['batch_id'],)), cutoff - timedelta(microseconds=1))

    def test_sql_future_generation_time_is_still_rejected(self):
        cutoff = datetime.now(timezone.utc) + timedelta(minutes=3)
        item = self.experiment(cutoff)
        rid, report = self.synthetic_report(item, cutoff - timedelta(microseconds=1), 'VERIFIED')
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.attest(item, rid, report)

    def test_forward_upgrade_preserves_old_equal_proof_and_scores_but_excludes_eligibility(self):
        migration = importlib.import_module('future_intelligence.db.migrate')
        database = 'fi_boundary_upgrade_' + uuid4().hex[:14]
        admin_dsn = make_conninfo(fixture.DSN, dbname=database)
        with psycopg.connect(fixture.DSN, autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        try:
            with tempfile.TemporaryDirectory() as directory:
                package = Path(directory)
                target = package / 'migrations'
                target.mkdir()
                for path in files('future_intelligence.db').joinpath('migrations').iterdir():
                    if path.name.endswith('.sql') and path.name < '0018':
                        (target / path.name).write_bytes(path.read_bytes())
                with patch.object(migration, 'files', return_value=package):
                    migration.migrate(admin_dsn)
                scope = self.scope(database)
                cutoff = datetime.now(timezone.utc) + timedelta(seconds=30)
                item = self.experiment(cutoff, outcome=cutoff + timedelta(microseconds=1), scope=scope)
                rid, report = self.synthetic_report(item, cutoff, 'VERIFIED')
                self.attest(item, rid, report)  # Legacy <= behavior, only in this disposable database.
                operator = scope['d']['operator']
                self.assertEqual(operator.one('SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s',
                    (item['registration']['batch_id'],)), cutoff)
                operator.seal(operator.capture(item['experiment'], 'FORECAST'))
                old_run = scope['d']['scorer'].score(item['experiment'])
                with psycopg.connect(admin_dsn) as c:
                    old_question = c.execute('SELECT to_jsonb(v) FROM fi.question_versions v WHERE id=%s',
                                             (item['version'],)).fetchone()[0]
                    old_report = c.execute('SELECT to_jsonb(v) FROM fi.anchor_verifications v WHERE request_id=%s',
                                           (rid,)).fetchone()[0]
                    old_score = c.execute('SELECT to_jsonb(r) FROM fi.score_runs r WHERE id=%s', (old_run,)).fetchone()[0]
                    old_migrations = c.execute('SELECT version,sha256,applied_at FROM fi_meta.schema_migrations ORDER BY version').fetchall()
                self.assertEqual(old_score['algorithm'], 'fi.score.v1')
                self.assertEqual(migration.migrate(admin_dsn), ['0018_exclusive_forecast_boundary.sql'])
                with psycopg.connect(admin_dsn) as c:
                    self.assertEqual(c.execute('SELECT to_jsonb(v) FROM fi.question_versions v WHERE id=%s',
                                     (item['version'],)).fetchone()[0], old_question)
                    self.assertEqual(c.execute('SELECT to_jsonb(v) FROM fi.anchor_verifications v WHERE request_id=%s',
                                     (rid,)).fetchone()[0], old_report)
                    self.assertEqual(c.execute('SELECT to_jsonb(r) FROM fi.score_runs r WHERE id=%s',
                                     (old_run,)).fetchone()[0], old_score)
                    self.assertEqual(c.execute("SELECT version,sha256,applied_at FROM fi_meta.schema_migrations "
                                     "WHERE version<'0018' ORDER BY version").fetchall(), old_migrations)
                self.assertIsNone(operator.one('SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s',
                                               (item['registration']['batch_id'],)))
                new_run = scope['d']['scorer'].score(item['experiment'])
                self.assertNotEqual(old_run, new_run)
                self.assertEqual(scope['d']['scorer'].one('SELECT algorithm FROM fi.score_runs WHERE id=%s',
                                                        (new_run,)), 'fi.score.v2')
                new_item = self.question(datetime.now(timezone.utc) + timedelta(days=1), scope=scope)
                self.assertTrue(scope['d']['author'].one(
                    'SELECT forecast_deadline=outcome_not_before FROM fi.question_catalog WHERE id=%s',
                    (new_item['version'],)))
        finally:
            # Only the uniquely named synthetic database created above is removed.
            with psycopg.connect(fixture.DSN, autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
