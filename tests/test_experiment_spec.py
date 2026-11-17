from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from future_intelligence.tasks.spec import validate_registration, uniform_ppm
from future_intelligence.tasks.repository import TaskRepository, uuid_text
from future_intelligence.tasks.fixture_worker import run_once


def experiment_spec(**changes):
    spec = dict(protocol='fi.experiment.v1', suite='A', run_kind='FIXTURE',
                forecast_deadline=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
                budget_microusd=1000, attempt_cap_microusd=100, max_attempts=2, lease_seconds=30,
                failure_policy='REPORT_MISSING', baseline_policy='UNIFORM_CATEGORIES_V1',
                disclosure_policy='COMPLETE_BATCH_AFTER_RESOLUTION')
    spec.update(changes)
    return spec


def entries(count=1):
    return [dict(entry_key=f'model-{i}', provider='fixture', model_id=f'fixture-{i}',
                 prompt_sha256='a'*64, parameters={}) for i in range(count)]


class ExperimentSpecTests(unittest.TestCase):
    def valid(self, spec=None, models=None, versions=None):
        return validate_registration(versions or [str(uuid4())], models or entries(), spec or experiment_spec())

    def test_roundtrip_and_isolation(self):
        models = entries(2); spec = experiment_spec()
        v, e, s = self.valid(spec, models)
        self.assertEqual(validate_registration(v, e, s), (v, e, s))
        e[0]['parameters']['changed'] = True
        self.assertEqual(models[0]['parameters'], {})

    def test_order_is_stable(self):
        versions = [str(uuid4()), str(uuid4())]; models = entries(2); spec = experiment_spec()
        self.assertEqual(self.valid(spec, models, versions), self.valid(spec, models[::-1], versions[::-1]))

    def test_missing_and_extra_fields(self):
        for field in experiment_spec():
            spec=experiment_spec(); del spec[field]
            with self.subTest(field=field),self.assertRaises(ValueError): self.valid(spec)
        with self.assertRaises(ValueError): self.valid(experiment_spec(locked_at='forged'))

    def test_integer_limits_and_bool(self):
        for k in ('budget_microusd','attempt_cap_microusd','max_attempts','lease_seconds'):
            for val in (0,-1,True,1.5,'10',10**13,None):
                with self.subTest(k=k,v=val),self.assertRaises(ValueError): self.valid(experiment_spec(**{k:val}))

    def test_attempt_cap(self):
        with self.assertRaises(ValueError): self.valid(experiment_spec(attempt_cap_microusd=1001))

    def test_protocol_policies(self):
        for k in ('protocol','suite','failure_policy','baseline_policy','disclosure_policy','run_kind'):
            with self.subTest(k=k),self.assertRaises(ValueError): self.valid(experiment_spec(**{k:'unsupported'}))

    def test_deadline(self):
        for val in ('2026-01-01T00:00:00','invalid','2026-99-01T00:00:00Z',None):
            with self.subTest(v=val),self.assertRaises(ValueError): self.valid(experiment_spec(forecast_deadline=val))

    def test_replay_does_not_trust_client_clock(self):
        self.valid(experiment_spec(forecast_deadline='2000-01-01T00:00:00Z'))  # DB decides admission.

    def test_duplicate_versions(self):
        v=str(uuid4())
        with self.assertRaises(ValueError): self.valid(versions=[v,v])

    def test_entry_validation(self):
        for k,val in (('entry_key','../bad'),('model_id',''),('provider','openai'),('prompt_sha256','wrong'),('parameters',[])):
            models=entries(); models[0][k]=val
            with self.subTest(k=k),self.assertRaises(ValueError): self.valid(models=models)

    def test_duplicate_entries(self):
        with self.assertRaises(ValueError): self.valid(models=entries()*2)

    def test_credentials_denied(self):
        for key in ('api_key','APIKEY','password','Authorization','token','secret'):
            models=entries(); models[0]['parameters'][key]='fixture-not-a-secret'
            with self.subTest(key=key),self.assertRaises(ValueError): self.valid(models=models)

    def test_nonfinite_denied(self):
        models=entries(); models[0]['parameters']['temperature']=float('nan')
        with self.assertRaises(ValueError): self.valid(models=models)

    def test_live_kind_requires_real_provider_identity(self):
        with self.assertRaises(ValueError): self.valid(experiment_spec(run_kind='LIVE'))
        models=entries(); models[0]['provider']='google'
        self.valid(experiment_spec(run_kind='LIVE'),models)

    def test_uniform_ppm(self):
        for n in range(2,102):
            result=uniform_ppm(n)
            self.assertEqual(sum(result),1000000)
            self.assertEqual(len(result),n)
            self.assertLessEqual(max(result)-min(result),1)
        for n in (True,1,102,2.0):
            with self.assertRaises(ValueError): uniform_ppm(n)

    def test_fencing_input(self):
        lease=dict(task_id=str(uuid4()),attempt_id=str(uuid4()),fence=1)
        self.assertEqual(TaskRepository._identity(lease)[2],1)
        lease['fence']=True
        with self.assertRaises(ValueError): TaskRepository._identity(lease)

    def test_fixture_runner_cannot_fabricate_live(self):
        class Fake:
            def claim(self, experiment, run_kind):
                self.kind=run_kind
                return {'run_kind':'LIVE'}
        repository=Fake()
        with self.assertRaises(RuntimeError): run_once(repository,'fixture')
        self.assertEqual(repository.kind,'FIXTURE')

    def test_fixture_runner_does_not_dispatch_twice(self):
        class Fake:
            def claim(self, experiment, run_kind): return {'run_kind':'FIXTURE'}
            def start_call(self, lease): return False
        with self.assertRaises(RuntimeError): run_once(Fake(),'fixture')

    def test_native_uuid_identifiers_and_cancel(self):
        value=uuid4()
        self.assertEqual(uuid_text(value),str(value))
        self.assertEqual(uuid_text(str(value)),str(value))
        lease={'task_id':value,'attempt_id':uuid4(),'fence':1}
        self.assertEqual(TaskRepository._identity(lease)[0],str(value))
        class Capture(TaskRepository):
            def _call(self, statement, params=()):
                self.params=params
        repo=Capture('unused-fixture')
        repo.cancel(value,'fixture cancellation')
        self.assertEqual(repo.params,(str(value),'fixture cancellation'))

    def test_non_uuid_identifier_rejected(self):
        for value in (None,False,12,{},'not-a-uuid'):
            with self.subTest(value=value),self.assertRaises(ValueError): uuid_text(value)
