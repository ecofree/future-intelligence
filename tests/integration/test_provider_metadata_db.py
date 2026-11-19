"""Real PostgreSQL, synthetic observations only; no provider calls or invoices.

Upgrade coverage creates and drops only its own uniquely named test database.
Historical observations are never rewritten to pretend metadata was captured.
"""
import copy
import hashlib
import importlib
from importlib.resources import files
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

import test_live_evidence as base
import test_compatible_providers as compatible_fixture
from future_intelligence.db.repository import QuestionRepository
from future_intelligence.evidence.repository import EvidenceRepository
from future_intelligence.providers.live import CURRENT_PROMPT_SHA256, terra_generation_profile
from future_intelligence.tasks.repository import TaskRepository


MIGRATION = '0019_provider_observation_metadata.sql'
FIELDS = ('reasoning_effort', 'reasoning_mode', 'service_tier')


def observation(version=2):
    result = dict(protocol='fi.provider-observation.v1', status='OK', http_status=200,
                  response_sha256=hashlib.sha256(b'SYNTHETIC provider reply').hexdigest(),
                  model_reported='gpt-6.1-terra', request_id='req_synthetic_wire',
                  usage=dict(input_tokens=123, output_tokens=45, cached_input_tokens=0),
                  output=dict(probabilities_ppm=[300000, 700000],
                              rationale='SYNTHETIC metadata regression, not a real forecast.',
                              evidence_ids=['e1']))
    if version == 2:
        result.update(protocol='fi.provider-observation.v2', response_id='resp_synthetic_body',
                      generation_reported={k: {'state': 'PRESENT', 'value': v}
                                           for k, v in zip(FIELDS, ('medium', 'standard', 'default'))})
    return result


class ProviderMetadataDBTests(unittest.TestCase):
    setUpClass = classmethod(base.LiveEvidenceTests.setUpClass.__func__)
    tearDownClass = classmethod(base.LiveEvidenceTests.tearDownClass.__func__)
    call = base.LiveEvidenceTests.call
    admin = base.LiveEvidenceTests.admin
    question = base.LiveEvidenceTests.question
    claim = base.LiveEvidenceTests.claim
    prepare = base.LiveEvidenceTests.prepare

    def make(self, *, provider='openai', generation=True, model=None, entry=None):
        original = base.fixture.entry

        def config(name):
            value = copy.deepcopy(entry) if entry is not None else original(name)
            value['prompt_sha256'] = CURRENT_PROMPT_SHA256
            if entry is None and provider == 'openai':
                value['model_id'] = model or 'gpt-6.1-terra'
                if generation:
                    value['parameters']['generation'] = terra_generation_profile()
            return value

        with patch.object(base.fixture, 'entry', side_effect=config):
            return base.LiveEvidenceTests.make(self, provider)

    def stored(self, lease):
        return self.admin('SELECT observation FROM fi.provider_observations WHERE attempt_id=%s',
                          (lease['attempt_id'],))

    def test_v1_remains_valid_and_cannot_be_backfilled_as_v2(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        old = observation(1)
        digest = self.er['worker'].observe(lease, old)
        self.assertEqual(self.er['worker'].observe(lease, copy.deepcopy(old)), digest)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].observe(lease, observation())
        self.assertEqual(self.stored(lease), old)
        self.assertNotIn('response_id', self.stored(lease))
        self.assertNotIn('generation_reported', self.stored(lease))

    def test_v2_persists_separate_ids_without_changing_request_or_unknown_cost(self):
        experiment, *_ = self.make()
        lease, body = self.prepare(experiment)
        obs = observation()
        self.er['worker'].observe(lease, obs)
        self.assertEqual(self.stored(lease), obs)
        self.assertEqual(self.admin('SELECT request_text FROM fi.provider_requests WHERE attempt_id=%s',
                                    (lease['attempt_id'],)), body)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].succeed(lease, obs['output']['probabilities_ppm'], obs['response_sha256'], 1)
        self.er['worker'].succeed(lease, obs['output']['probabilities_ppm'], obs['response_sha256'], None)
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (lease['attempt_id'],)), 'HELD_UNKNOWN')
        self.assertIsNone(self.admin('SELECT actual_microusd FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (lease['attempt_id'],)))

    def test_missing_and_invalid_metadata_are_explicit_null_not_output_failure(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        obs = observation()
        obs['request_id'] = None
        obs['response_id'] = None
        obs['generation_reported']['reasoning_effort'] = {'state': 'MISSING', 'value': None}
        obs['generation_reported']['reasoning_mode'] = {'state': 'INVALID', 'value': None}
        self.er['worker'].observe(lease, obs)
        self.assertEqual(self.stored(lease), obs)
        self.er['worker'].succeed(lease, obs['output']['probabilities_ppm'], obs['response_sha256'], None)

    def test_transport_failure_preserves_missing_metadata_and_unknown_reservation(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        obs = observation()
        obs.update(status='TIMEOUT', http_status=None, response_sha256=None,
                   model_reported=None, request_id=None, response_id=None, output=None,
                   usage=dict(input_tokens=None, output_tokens=None, cached_input_tokens=None),
                   generation_reported={k: {'state': 'MISSING', 'value': None} for k in FIELDS})
        self.er['worker'].observe(lease, obs)
        self.er['worker'].fail(lease, 'TIMEOUT', None, False)
        self.assertEqual(self.stored(lease), obs)
        self.assertEqual(self.admin('SELECT status FROM fi.task_states WHERE task_id=%s',
                                    (lease['task_id'],)), 'UNCERTAIN')
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (lease['attempt_id'],)), 'HELD_UNKNOWN')

    def test_closed_shapes_and_unredacted_metadata_are_rejected(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        bad = []
        for key in ('response_id', 'generation_reported'):
            candidate = observation(); del candidate[key]; bad.append(candidate)
        for value in ('contains whitespace', 'x' * 201, 7, True, {}, []):
            candidate = observation(); candidate['response_id'] = value; bad.append(candidate)
        candidate = observation(); candidate['raw_reasoning'] = 'SYNTHETIC unapproved field'; bad.append(candidate)
        for value in (None, [], {}, {'state': 'MISSING', 'value': None}):
            candidate = observation(); candidate['generation_reported'] = value; bad.append(candidate)
        candidate = observation(); del candidate['generation_reported']['reasoning_mode']; bad.append(candidate)
        candidate = observation(); candidate['generation_reported']['extra'] = None; bad.append(candidate)
        for item in (None, [], {}, {'state': 'PRESENT'}, {'value': 'medium'},
                     {'state': 'PRESENT', 'value': 'medium', 'raw': 'SYNTHETIC unapproved field'},
                     {'state': 'UNKNOWN', 'value': None}, {'state': None, 'value': None},
                     {'state': 'PRESENT', 'value': None}, {'state': 'PRESENT', 'value': 1},
                     {'state': 'PRESENT', 'value': 'SYNTHETIC unapproved text'},
                     {'state': 'MISSING', 'value': 'medium'}, {'state': 'INVALID', 'value': 'raw text'}):
            candidate = observation(); candidate['generation_reported']['reasoning_effort'] = item; bad.append(candidate)
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.er['worker'].observe(lease, candidate)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_observations WHERE attempt_id=%s',
                                    (lease['attempt_id'],)), 0)
        self.er['worker'].observe(lease, observation())

    def test_each_metadata_enum_is_bounded_independently(self):
        enums = {'reasoning_effort': ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
                 'reasoning_mode': ('standard', 'pro'),
                 'service_tier': ('auto', 'default', 'flex', 'priority', 'scale')}
        for key, values in enums.items():
            for value in values:
                report = observation()['generation_reported']
                report[key] = {'state': 'PRESENT', 'value': value}
                self.assertIs(self.admin('SELECT fi.valid_provider_generation_report(%s)', (Jsonb(report),)), True)
            report = observation()['generation_reported']
            report[key] = {'state': 'PRESENT', 'value': 'unregistered-value'}
            self.assertIs(self.admin('SELECT fi.valid_provider_generation_report(%s)', (Jsonb(report),)), False)
        for value in (None, [], {}, 'SYNTHETIC text'):
            self.assertIs(self.admin('SELECT fi.valid_provider_generation_report(%s)', (Jsonb(value),)), False)

    def test_v2_requires_actual_official_terra_entry_not_reported_model(self):
        cases = [dict(generation=False), dict(generation=False, model='synthetic-other-model'),
                 dict(provider='anthropic'),
                 dict(provider='openai_compatible', entry=compatible_fixture.entry(model='gpt-6.1-terra'))]
        for config in cases:
            with self.subTest(config=config):
                experiment, *_ = self.make(**config)
                lease, _ = self.prepare(experiment)
                # This claimed model and valid-looking metadata cannot override
                # the registered provider/model/explicit-generation identity.
                with self.assertRaises(psycopg.errors.InvalidParameterValue):
                    self.er['worker'].observe(lease, observation())
                self.er['worker'].observe(lease, observation(1))
                self.assertEqual(self.stored(lease)['protocol'], 'fi.provider-observation.v1')

    def test_existing_output_trigger_still_checks_v2(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        for key, value in [('probabilities_ppm', [1000000]), ('probabilities_ppm', [1, 1]),
                           ('probabilities_ppm', [1000001, -1]), ('probabilities_ppm', [True, 999999]),
                           ('evidence_ids', ['e1', 'e1']), ('evidence_ids', ['unknown'])]:
            obs = observation(); obs['output'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.er['worker'].observe(lease, obs)
        self.er['worker'].observe(lease, observation())

    def test_v2_keeps_attempt_ownership_fencing_and_registered_request_gate(self):
        experiment, *_ = self.make()
        lease = self.claim(experiment)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.er['worker'].observe(lease, observation())
        body = base.fixture.build_request(lease['entry'], self.er['worker'].context(lease))
        self.er['worker'].prepare(lease, body)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.er['worker2'].observe(lease, observation())
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.er['worker'].observe({**lease, 'fence': lease['fence'] + 1}, observation())
        self.er['worker'].observe(lease, observation())

    def test_v2_replay_is_idempotent_and_metadata_conflicts_do_not_replace_history(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        obs = observation()
        digest = self.er['worker'].observe(lease, obs)
        self.assertEqual(self.er['worker'].observe(lease, copy.deepcopy(obs)), digest)
        for key, value in [('request_id', 'req_other'), ('response_id', 'resp_other'),
                           ('generation_reported', {k: {'state': 'MISSING', 'value': None} for k in FIELDS})]:
            candidate = copy.deepcopy(obs); candidate[key] = value
            with self.subTest(key=key), self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                self.er['worker'].observe(lease, candidate)
        self.assertEqual(self.stored(lease), obs)
        self.assertEqual(self.admin("SELECT count(*) FROM fi.outbox_events WHERE task_id=%s AND kind='PROVIDER_OBSERVED'",
                                    (lease['task_id'],)), 1)

    def test_late_v2_observation_does_not_revive_lease(self):
        experiment, *_ = self.make()
        lease, _ = self.prepare(experiment)
        # Mutable test state simulates expiry without sleeping or forging a proof.
        self.admin("UPDATE fi.task_states SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s RETURNING task_id",
                   (lease['task_id'],))
        obs = observation(); self.er['worker'].observe(lease, obs)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].succeed(lease, obs['output']['probabilities_ppm'], obs['response_sha256'], None)
        self.assertEqual(self.stored(lease), obs)

    def test_capability_is_worker_only_and_validator_is_private(self):
        self.assertIs(self.er['worker'].supports_observation_v2(), True)
        self.assertIs(self.call('worker', 'SELECT fi.provider_observation_v2_supported()'), True)
        for role in ('author', 'operator', 'resolver'):
            with self.subTest(role=role), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.call(role, 'SELECT fi.provider_observation_v2_supported()')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker', 'SELECT fi.valid_provider_generation_report(%s)',
                      (Jsonb(observation()['generation_reported']),))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker', 'SELECT count(*) FROM fi.provider_observations')

    def test_upgrade_preserves_v1_rows_and_ledger_before_allowing_new_v2(self):
        migration_module = importlib.import_module('future_intelligence.db.migrate')
        database = 'fi_metadata_upgrade_' + uuid4().hex
        owner_dsn = make_conninfo(base.previous.DSN, dbname=database)
        created = False
        try:
            with psycopg.connect(base.previous.DSN, autocommit=True) as c:
                c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
                created = True
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); directory = root / 'migrations'; directory.mkdir()
                sources = files('future_intelligence.db').joinpath('migrations')
                for source in sources.iterdir():
                    if source.name.endswith('.sql') and source.name < MIGRATION:
                        (directory / source.name).write_bytes(source.read_bytes())
                with patch.object(migration_module, 'files', return_value=root):
                    installed = migration_module.migrate(owner_dsn)
                self.assertEqual(len(installed), 19)
            dsns = {role: make_conninfo(dsn, dbname=database) for role, dsn in self.dsns.items()}

            def read_copy(statement, parameters=()):
                with psycopg.connect(owner_dsn) as c:
                    return c.execute(statement, parameters).fetchone()[0]

            with patch.multiple(self, dsns=dsns,
                                r={role: TaskRepository(dsn) for role, dsn in dsns.items()},
                                er={role: EvidenceRepository(dsn) for role, dsn in dsns.items()},
                                author=QuestionRepository(dsns['author']),
                                reviewer=QuestionRepository(dsns['reviewer'])), \
                 patch.object(self, 'admin', new=read_copy):
                self.assertIs(self.er['worker'].supports_observation_v2(), False)
                self.assertIsNone(self.call('worker', "SELECT to_regprocedure('fi.provider_observation_v2_supported()')"))
                old_experiment, *_ = self.make()
                old_lease, _ = self.prepare(old_experiment)
                old = observation(1)
                digest = self.er['worker'].observe(old_lease, old)
                self.er['worker'].succeed(old_lease, old['output']['probabilities_ppm'], old['response_sha256'], None)
                next_experiment, *_ = self.make()
                next_lease, _ = self.prepare(next_experiment)
                with self.assertRaises(psycopg.errors.InvalidParameterValue):
                    self.er['worker'].observe(next_lease, observation())
                snapshot_sql = """
                    SELECT jsonb_build_object('request',to_jsonb(r),'observation',to_jsonb(o),
                      'attempt',to_jsonb(a),'reservation',to_jsonb(b),'account',to_jsonb(c))
                    FROM fi.provider_requests r JOIN fi.provider_observations o USING(attempt_id)
                    JOIN fi.task_attempts a ON a.id=r.attempt_id
                    JOIN fi.budget_reservations b ON b.attempt_id=r.attempt_id
                    JOIN fi.expected_tasks t ON t.id=r.task_id
                    JOIN fi.budget_accounts c ON c.experiment_id=t.experiment_id
                    WHERE r.attempt_id=%s
                """
                before = self.admin(snapshot_sql, (old_lease['attempt_id'],))
                migration_sql = 'SELECT jsonb_agg(to_jsonb(m) ORDER BY version) FROM fi_meta.schema_migrations m WHERE version<%s'
                migration_rows = self.admin(migration_sql, (MIGRATION,))
                trigger_sql = """SELECT jsonb_build_object('definition',pg_get_triggerdef(oid),'enabled',tgenabled)
                    FROM pg_trigger WHERE tgrelid='fi.provider_observations'::regclass
                      AND tgname='provider_observation_contract'"""
                trigger = self.admin(trigger_sql)
                # This case owns the 0019 upgrade contract. Later migrations
                # have separate upgrade tests and must not silently join it.
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp); directory = root / 'migrations'; directory.mkdir()
                    for source in files('future_intelligence.db').joinpath('migrations').iterdir():
                        if source.name.endswith('.sql') and source.name <= MIGRATION:
                            (directory / source.name).write_bytes(source.read_bytes())
                    with patch.object(migration_module, 'files', return_value=root):
                        self.assertEqual(migration_module.migrate(owner_dsn), [MIGRATION])
                        self.assertEqual(migration_module.migrate(owner_dsn), [])
                self.assertEqual(self.admin(snapshot_sql, (old_lease['attempt_id'],)), before)
                self.assertEqual(self.admin(migration_sql, (MIGRATION,)), migration_rows)
                self.assertEqual(self.admin(trigger_sql), trigger)
                self.assertIs(self.er['worker'].supports_observation_v2(), True)
                self.assertIs(self.call('worker', 'SELECT fi.provider_observation_v2_supported()'), True)
                self.assertEqual(self.er['worker'].observe(old_lease, old), digest)
                with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                    self.er['worker'].observe(old_lease, observation())
                self.er['worker'].observe(next_lease, observation())
                self.assertEqual(self.stored(next_lease), observation())
        finally:
            if created:
                with psycopg.connect(base.previous.DSN, autocommit=True) as c:
                    c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
