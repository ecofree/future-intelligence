"""Disposable PostgreSQL and local TEST CA only; no provider or public TSA calls.

The isolated verifier's shape tests intentionally submit malformed reports.
Successful reports use real ephemeral RFC3161 signatures and local signed CRLs.
"""
import base64
import copy
from dataclasses import asdict
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

import test_operational_admission as base
from tsa_fixture import fixture_crl, timestamp
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.delivery.timestamps import CRLTrustProfile, TrustProfile, verify_reply
from future_intelligence.evidence.repository import EvidenceRepository
from future_intelligence.tasks.repository import TaskRepository

MIGRATION = '0020_crl_time_admission.sql'
OWNER_DSN = base.previous.previous.DSN


class TSARevocationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.OperationalAdmissionTests.setUpClass.__func__(cls)
        login = 'fi_crl_scorer_' + uuid4().hex[:10]
        password = uuid4().hex
        with psycopg.connect(OWNER_DSN) as c:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT PASSWORD {}').format(
                sql.Identifier(login), sql.Literal(password)))
            c.execute(sql.SQL('GRANT fi_scorekeeper TO {}').format(sql.Identifier(login)))
        cls.logins['scorer'] = login
        cls.dsns['scorer'] = make_conninfo(OWNER_DSN, user=login, password=password)

    tearDownClass = classmethod(base.OperationalAdmissionTests.tearDownClass.__func__)
    call = base.OperationalAdmissionTests.call
    admin = base.OperationalAdmissionTests.admin
    question = base.OperationalAdmissionTests.question
    make = base.OperationalAdmissionTests.make
    checks = base.OperationalAdmissionTests.checks
    confirm = base.OperationalAdmissionTests.confirm
    registered = base.OperationalAdmissionTests.registered
    claim = base.OperationalAdmissionTests.claim
    prepare = base.OperationalAdmissionTests.prepare

    def legacy_profile(self):
        return TrustProfile(**{k: v for k, v in asdict(self.profile).items()
                               if k not in ('revocation_policy', 'max_crl_age_seconds')})

    def register_with(self, profile=None):
        profile = profile or self.profile
        with patch.object(self, 'profile_json', json.loads(json.dumps(asdict(profile)))):
            return self.registered()

    def attest(self, request, report):
        self.call('verifier', 'SELECT fi.attest_anchor(%s,%s)', (request, Jsonb(report)))

    def anchor(self, experiment, batch, *, profile=None, crl=None, save=True):
        profile = profile or self.profile
        if isinstance(profile, CRLTrustProfile) and crl is None:
            crl = self.crl
        repo = DeliveryRepository(self.dsns['operator'])
        request, query = repo.new_anchor(batch)
        reply = timestamp(self.tsa, query)
        repo.receive(request, reply)
        report = verify_reply(repo.get_batch(batch)['subject_sha256'], query, reply,
                              self.ca, profile,
                              self.admin('SELECT deadline FROM fi.experiments WHERE id=%s', (experiment,)),
                              crl_bundle=crl)
        if save:
            self.attest(request, report)
        return request, report

    def wait_upper(self, report):
        upper = datetime.fromisoformat(report['upper_time'])
        time.sleep(max(0, (upper - datetime.now(timezone.utc)).total_seconds()) + .02)

    def gate(self, experiment):
        return self.call('worker', 'SELECT fi.worker_delivery_gate(%s)', (experiment,))['ready']

    def current_upper(self, batch):
        return self.call('operator', 'SELECT verified_upper_time FROM fi.batch_catalog WHERE id=%s', (batch,))

    def complete_fixture_call(self, experiment):
        lease, body = self.prepare(experiment)
        observation = base.previous.fixture.parse_response(
            'openai', base.previous.fixture.response(), 2, {'e1'})
        self.er['worker'].observe(lease, observation)
        self.er['worker'].succeed(lease, observation['output']['probabilities_ppm'],
                                  observation['response_sha256'], None)
        return lease, body

    def forecast(self, experiment, **anchor_options):
        repo = DeliveryRepository(self.dsns['operator'])
        batch = repo.capture(experiment, 'FORECAST')
        repo.seal(batch)
        request, report = self.anchor(experiment, batch, **anchor_options)
        return batch, request, report

    def score(self, experiment):
        run = DeliveryRepository(self.dsns['scorer']).score(experiment)
        context = json.loads(self.admin('SELECT context_text FROM fi.score_runs WHERE id=%s', (run,)))
        return run, context

    def test_legacy_nonfixture_report_is_preserved_but_cannot_admit_formal(self):
        profile = self.legacy_profile()
        experiment, _, _, batch = self.register_with(profile)
        request, report = self.anchor(experiment, batch, profile=profile)
        self.wait_upper(report)
        self.assertEqual(report['revocation_status'], 'NOT_CHECKED')
        self.assertIsNone(self.current_upper(batch))
        self.assertFalse(self.gate(experiment))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.claim(experiment)
        task = self.admin('SELECT id FROM fi.expected_tasks WHERE experiment_id=%s', (experiment,))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].claim(experiment, 'LIVE', task_id=str(task))
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_attempts WHERE task_id=%s', (task,)), 0)
        self.attest(request, report)
        self.assertEqual(self.admin('SELECT report FROM fi.anchor_verifications WHERE request_id=%s', (request,)), report)

    def test_fresh_crl_allows_claim_and_prepare_without_claiming_known_cost(self):
        experiment, _, _, batch = self.registered()
        request, report = self.anchor(experiment, batch)
        self.wait_upper(report)
        self.assertEqual(len(report), 16)
        self.assertEqual(report['revocation_status'], 'GOOD')
        self.attest(request, copy.deepcopy(report))
        self.assertTrue(self.gate(experiment))
        lease, body = self.complete_fixture_call(experiment)
        self.assertEqual(self.admin('SELECT request_text FROM fi.provider_requests WHERE attempt_id=%s',
                                    (lease['attempt_id'],)), body)
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (lease['attempt_id'],)), 'HELD_UNKNOWN')
        self.assertIsNone(self.admin('SELECT actual_microusd FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (lease['attempt_id'],)))
        self.assertEqual(self.admin('SELECT count(*) FROM fi.anchor_verifications WHERE request_id=%s', (request,)), 1)

    def test_trust_profile_accepts_only_exact_legacy_or_explicit_crl_contract(self):
        for profile in (asdict(self.legacy_profile()), asdict(self.profile)):
            self.admin('SELECT fi.validate_delivery_plan(%s,%s)', (Jsonb(base.PLAN), Jsonb(profile)))
        invalid = []
        for value in (None, True, '3600', 0, -1, 31536001, 1.5):
            invalid.append({**asdict(self.profile), 'max_crl_age_seconds': value})
        for value in (None, 'unchecked', True):
            invalid.append({**asdict(self.profile), 'revocation_policy': value})
        for key in ('max_crl_age_seconds', 'revocation_policy'):
            candidate = asdict(self.profile); del candidate[key]; invalid.append(candidate)
        invalid.append({**asdict(self.profile), 'extra': 'not preregistered'})
        for candidate in invalid:
            with self.subTest(profile=candidate), self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.admin('SELECT fi.validate_delivery_plan(%s,%s)', (Jsonb(base.PLAN), Jsonb(candidate)))

    def test_report_shape_hash_encoding_and_crl_count_fail_closed(self):
        experiment, _, _, batch = self.registered()
        request, report = self.anchor(experiment, batch, save=False)
        invalid = [None, [], {**report, 'extra': 'not part of the report'}]
        for key in report:
            candidate = dict(report); del candidate[key]; invalid.append(candidate)
        for key, value in [('protocol', None), ('status', None), ('verifier', 'unchecked'),
                           ('revocation_status', 'NOT_CHECKED'), ('crl_base64', None),
                           ('crl_base64', 'not base64!'), ('crl_sha256', '0' * 64),
                           ('crl_valid_from', None), ('crl_valid_until', None),
                           ('crl_valid_from', '2026-11-19T00:00:00'),
                           ('crl_valid_until', '2026-99-99T00:00:00Z')]:
            invalid.append({**report, key: value})
        for raw in (b'no PEM CRL', self.crl * 9, b'x' * 2000001):
            invalid.append({**report, 'crl_base64': base64.b64encode(raw).decode('ascii'),
                            'crl_sha256': hashlib.sha256(raw).hexdigest()})
        invalid.append({**report, 'crl_base64': report['crl_base64'] + '\n'})
        for index, candidate in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.attest(request, candidate)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.anchor_verifications WHERE request_id=%s', (request,)), 0)
        self.attest(request, report)

    def test_profile_report_version_binding_and_conflicts_do_not_rewrite_proofs(self):
        experiment, _, _, batch = self.registered()
        request, report = self.anchor(experiment, batch, save=False)
        legacy = {k: v for k, v in report.items() if not k.startswith('crl_')}
        legacy.update(protocol='fi.rfc3161-verification.v1', verifier='openssl-ts+fi-tstinfo-v1',
                      revocation_status='NOT_CHECKED')
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.attest(request, legacy)
        self.attest(request, report)
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.attest(request, {**report, 'crl_sha256': '0' * 64})
        self.assertEqual(self.admin('SELECT report FROM fi.anchor_verifications WHERE request_id=%s', (request,)), report)
        legacy_experiment, _, _, legacy_batch = self.register_with(self.legacy_profile())
        legacy_request, wrong_version = self.anchor(legacy_experiment, legacy_batch, save=False)
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.attest(legacy_request, wrong_version)

    def test_current_crl_window_and_recorded_check_bounds_are_exclusive_at_end(self):
        experiment, _, _, batch = self.registered()
        request, report = self.anchor(experiment, batch, save=False)
        now = datetime.now(timezone.utc)
        for start, end in [(now + timedelta(minutes=1), now + timedelta(minutes=2)),
                           (now - timedelta(minutes=2), now - timedelta(minutes=1)),
                           (now, now),
                           (now - timedelta(seconds=1), now + timedelta(seconds=3601))]:
            candidate = {**report, 'crl_valid_from': start.isoformat(), 'crl_valid_until': end.isoformat()}
            with self.subTest(start=start, end=end), self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.attest(request, candidate)
        # Exercise the INSERT guard as the isolated verifier, not the fixture
        # owner (a superuser is considered a member of every guarded role).
        # The grants, changed session identity and synthetic row all roll back.
        verifier = self.logins['verifier']

        def insert_at(check):
            with psycopg.connect(OWNER_DSN) as c:
                try:
                    c.execute(sql.SQL('GRANT INSERT ON fi.anchor_verifications TO {}').format(
                        sql.Identifier(verifier)))
                    c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION fi.ledger_rc() TO {}').format(
                        sql.Identifier(verifier)))
                    c.execute(sql.SQL('SET SESSION AUTHORIZATION {}').format(sql.Identifier(verifier)))
                    self.assertEqual(c.execute('SELECT session_user').fetchone()[0], verifier)
                    c.execute('''INSERT INTO fi.anchor_verifications(request_id,status,upper_time,report,verified_at)
                                 VALUES(%s,%s,%s,%s,%s)''',
                              (request, report['status'], report['upper_time'], Jsonb(report), check))
                finally:
                    c.rollback()

        privilege = (verifier, 'fi.anchor_verifications', 'INSERT')
        function_privilege = (verifier, 'fi.ledger_rc()', 'EXECUTE')
        self.assertFalse(self.admin('SELECT has_table_privilege(%s,%s,%s)', privilege))
        self.assertFalse(self.admin('SELECT has_function_privilege(%s,%s,%s)', function_privilege))
        for check in (datetime.fromisoformat(report['crl_valid_from']) - timedelta(microseconds=1),
                      datetime.fromisoformat(report['crl_valid_until'])):
            with self.subTest(check=check), self.assertRaises(psycopg.errors.InvalidParameterValue):
                insert_at(check)
        # The lower bound itself is inclusive; only the final real attestation
        # below persists, with its actual verification time.
        insert_at(report['crl_valid_from'])
        self.assertFalse(self.admin('SELECT has_table_privilege(%s,%s,%s)', privilege))
        self.assertFalse(self.admin('SELECT has_function_privilege(%s,%s,%s)', function_privilege))
        self.assertEqual(self.admin('SELECT count(*) FROM fi.anchor_verifications WHERE request_id=%s', (request,)), 0)
        self.attest(request, report)

    def test_expired_crl_blocks_new_claim_and_prepare_but_keeps_historical_score(self):
        completed = self.registered()
        claimed = self.registered()
        queued = self.registered()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        until = now + timedelta(seconds=15)
        crl = fixture_crl(Path(self.tmp.name), last_update=now - timedelta(seconds=1), next_update=until)
        request, report = self.anchor(completed[0], completed[3], crl=crl)
        _, claimed_report = self.anchor(claimed[0], claimed[3], crl=crl)
        _, queued_report = self.anchor(queued[0], queued[3], crl=crl)
        self.wait_upper(max((report, claimed_report, queued_report),
                            key=lambda item: datetime.fromisoformat(item['upper_time'])))
        self.complete_fixture_call(completed[0])
        held_lease = self.claim(claimed[0])
        held_body = base.previous.fixture.build_request(held_lease['entry'], self.er['worker'].context(held_lease))
        self.forecast(completed[0], crl=crl)
        first_run, context = self.score(completed[0])
        self.assertEqual(context['algorithm'], 'fi.score.v3')
        self.assertIs(context['eligible_time'], True)
        self.assertEqual(len(context['time_evidence']), 2)
        saved = self.admin('SELECT to_jsonb(v) FROM fi.anchor_verifications v WHERE request_id=%s', (request,))
        time.sleep(max(0, (until - datetime.now(timezone.utc)).total_seconds()) + .03)
        self.assertIsNone(self.current_upper(queued[3]))
        self.assertFalse(self.gate(queued[0]))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.claim(queued[0])
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].prepare(held_lease, held_body)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',
                                    (held_lease['attempt_id'],)), 0)
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.attest(request, report)
        self.assertEqual(self.score(completed[0]), (first_run, context))
        self.assertEqual(self.admin('SELECT to_jsonb(v) FROM fi.anchor_verifications v WHERE request_id=%s', (request,)), saved)

    def test_score_requires_actual_dispatch_window_and_preclaim_check(self):
        experiment, _, _, batch = self.registered()
        request, report = self.anchor(experiment, batch, save=False)
        self.wait_upper(report)
        # Make checked_at later than upper_time so the final negative isolates
        # a late verification, rather than failing only the TSA upper bound.
        self.attest(request, report)
        lease, _ = self.complete_fixture_call(experiment)
        self.forecast(experiment)
        _, valid = self.score(experiment)
        self.assertIs(valid['eligible_time'], True)
        # Mutable synthetic attempt state simulates an out-of-contract dispatch.
        # Verification rows and signed proof bytes remain untouched.
        self.admin('UPDATE fi.task_attempts SET call_started_at=%s WHERE id=%s RETURNING id',
                   (report['crl_valid_until'], lease['attempt_id']))
        _, outside = self.score(experiment)
        self.assertIs(outside['eligible_time'], False)
        checked = self.admin('SELECT verified_at FROM fi.anchor_verifications WHERE request_id=%s', (request,))
        self.assertGreater(checked - timedelta(microseconds=1), datetime.fromisoformat(report['upper_time']))
        self.admin('UPDATE fi.task_attempts SET claimed_at=%s,call_started_at=%s WHERE id=%s RETURNING id',
                   (checked - timedelta(microseconds=1), checked - timedelta(microseconds=1), lease['attempt_id']))
        _, too_early = self.score(experiment)
        self.assertIs(too_early['eligible_time'], False)

    def test_internal_time_view_remains_private(self):
        for role in ('operator', 'worker', 'verifier', 'scorer'):
            with self.subTest(role=role), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.call(role, 'SELECT count(*) FROM fi.anchor_time_catalog')
        self.assertFalse(self.admin("""SELECT EXISTS(SELECT 1 FROM pg_class c
            CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
            WHERE c.oid='fi.anchor_time_catalog'::regclass AND a.grantee=0)"""))

    def test_forward_upgrade_preserves_old_reports_ledger_and_score_versions(self):
        module = importlib.import_module('future_intelligence.db.migrate')
        database = 'fi_crl_upgrade_' + uuid4().hex
        owner = make_conninfo(OWNER_DSN, dbname=database)
        created = False

        def migrate_through(inclusive):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); directory = root / 'migrations'; directory.mkdir()
                for source in files('future_intelligence.db').joinpath('migrations').iterdir():
                    if source.name.endswith('.sql') and (source.name <= MIGRATION if inclusive else source.name < MIGRATION):
                        (directory / source.name).write_bytes(source.read_bytes())
                with patch.object(module, 'files', return_value=root):
                    return module.migrate(owner)

        def read_copy(statement, parameters=()):
            with psycopg.connect(owner) as c:
                return c.execute(statement, parameters).fetchone()[0]

        try:
            with psycopg.connect(OWNER_DSN, autocommit=True) as c:
                c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
                created = True
            self.assertEqual(len(migrate_through(False)), 20)
            dsns = {role: make_conninfo(dsn, dbname=database) for role, dsn in self.dsns.items()}
            legacy = self.legacy_profile()
            with patch.multiple(self, dsns=dsns,
                                r={role: TaskRepository(dsn) for role, dsn in dsns.items()},
                                er={role: EvidenceRepository(dsn) for role, dsn in dsns.items()},
                                author=QuestionRepository(dsns['author']), reviewer=QuestionRepository(dsns['reviewer'])), \
                 patch.object(self, 'admin', new=read_copy):
                experiment, _, _, batch = self.register_with(legacy)
                request, report = self.anchor(experiment, batch, profile=legacy)
                self.wait_upper(report)
                lease, _ = self.complete_fixture_call(experiment)
                self.forecast(experiment, profile=legacy)
                old_run, old_context = self.score(experiment)
                self.assertEqual(old_context['algorithm'], 'fi.score.v2')
                self.assertIs(old_context['eligible_time'], True)
                waiting, _, _, waiting_batch = self.register_with(legacy)
                _, waiting_report = self.anchor(waiting, waiting_batch, profile=legacy)
                self.wait_upper(waiting_report)
                waiting_lease = self.claim(waiting)
                waiting_body = base.previous.fixture.build_request(waiting_lease['entry'], self.er['worker'].context(waiting_lease))
                snapshot_sql = '''SELECT jsonb_build_object('proof',to_jsonb(v),'attempt',to_jsonb(a),
                    'reservation',to_jsonb(r),'account',to_jsonb(b),'score',to_jsonb(s))
                    FROM fi.anchor_verifications v CROSS JOIN fi.task_attempts a
                    JOIN fi.budget_reservations r ON r.attempt_id=a.id
                    JOIN fi.expected_tasks t ON t.id=a.task_id
                    JOIN fi.budget_accounts b ON b.experiment_id=t.experiment_id
                    CROSS JOIN fi.score_runs s WHERE v.request_id=%s AND a.id=%s AND s.id=%s'''
                before = self.admin(snapshot_sql, (request, lease['attempt_id'], old_run))
                checksums_sql = 'SELECT jsonb_agg(to_jsonb(m) ORDER BY version) FROM fi_meta.schema_migrations m WHERE version<%s'
                checksums = self.admin(checksums_sql, (MIGRATION,))
                self.assertEqual(migrate_through(True), [MIGRATION])
                self.assertEqual(migrate_through(True), [])
                self.assertEqual(self.admin(snapshot_sql, (request, lease['attempt_id'], old_run)), before)
                self.assertEqual(self.admin(checksums_sql, (MIGRATION,)), checksums)
                self.assertIsNone(self.current_upper(batch))
                self.assertFalse(self.gate(waiting))
                with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                    self.er['worker'].prepare(waiting_lease, waiting_body)
                new_run, new_context = self.score(experiment)
                self.assertNotEqual(new_run, old_run)
                self.assertEqual(new_context['algorithm'], 'fi.score.v3')
                self.assertIs(new_context['eligible_time'], False)
                self.assertEqual(self.admin('SELECT context_text::jsonb FROM fi.score_runs WHERE id=%s', (old_run,)), old_context)
                fresh, _, _, fresh_batch = self.registered()
                self.anchor(fresh, fresh_batch)
                self.assertIsNotNone(self.current_upper(fresh_batch))
        finally:
            if created:
                with psycopg.connect(OWNER_DSN, autocommit=True) as c:
                    c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
