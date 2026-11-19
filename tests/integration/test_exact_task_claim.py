"""Real PostgreSQL exact claims; synthetic providers and TSA only, no live API."""
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from uuid import uuid4

import psycopg
import test_operational_admission as base
import test_live_providers as fixture
from test_experiment_spec import experiment_spec, entries
from test_question_spec import example_spec
from future_intelligence.tasks.live_worker import run_once


class ExactTaskClaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): base.OperationalAdmissionTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.OperationalAdmissionTests.tearDownClass.__func__(cls)
    call = base.OperationalAdmissionTests.call
    admin = base.OperationalAdmissionTests.admin
    question = base.OperationalAdmissionTests.question
    make = base.OperationalAdmissionTests.make
    checks = base.OperationalAdmissionTests.checks
    confirm = base.OperationalAdmissionTests.confirm
    registered = base.OperationalAdmissionTests.registered
    anchored = base.OperationalAdmissionTests.anchored

    def make_pair(self, *, models=1, sealed=True, mode='DIAGNOSTIC', **changes):
        binary, permission = self.question()
        spec = example_spec()
        spec.update(question_type='binned_numeric', bin_edges=['3.0','3.5','4.0','4.5'], unit='fixture')
        binned = self.author.create_version('exact-bins-'+uuid4().hex, spec, [str(permission)])
        self.reviewer.review(binned, 'Synthetic exact-selection review'); self.author.freeze(binned)
        configs = [dict(fixture.entry(), entry_key=f'model-{i}') for i in range(models)]
        plan = experiment_spec(run_kind='LIVE', budget_microusd=100000000,
                               attempt_cap_microusd=1000000, lease_seconds=120)
        plan.update(changes)
        experiment = self.r['operator'].register('exact-'+uuid4().hex, [binary,binned], configs, plan)
        if mode is not None:
            self.call('operator', 'SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                      (experiment,mode,5,3600,'Synthetic exact task selection; no real provider'))
        self.r['operator'].bind_worker(experiment,'model-0',self.logins['worker'])
        self.r['operator'].bind_worker(experiment,'model-0' if models==1 else 'model-1',self.logins['worker2'])
        source = self.admin('SELECT source_id FROM fi.source_permissions WHERE id=%s',(permission,))
        if sealed:
            self.er['operator'].seal(experiment, [dict(question_version_id=version, items=[dict(
                id='e1', source_id=str(source), permission_id=str(permission),
                title='SYNTHETIC EXACT CLAIM', text='Synthetic evidence, not an actual release.')])
                for version in (binary,binned)])
        return experiment, permission

    def tasks(self, experiment, entry='model-0'):
        return self.admin("""SELECT jsonb_agg(jsonb_build_object('id',id,'version',question_version_id)
            ORDER BY id) FROM fi.expected_tasks WHERE experiment_id=%s AND entry_key=%s""", (experiment,entry))

    def attempts(self, experiment):
        return self.admin('''SELECT count(*) FROM fi.task_attempts a JOIN fi.expected_tasks t
            ON t.id=a.task_id WHERE t.experiment_id=%s''', (experiment,))

    def state(self, task):
        return self.admin('SELECT status FROM fi.task_states WHERE task_id=%s', (task,))

    def test_exact_claim_selects_larger_uuid_and_legacy_keeps_uuid_order(self):
        experiment, _ = self.make_pair()
        smaller, larger = self.tasks(experiment)
        lease = self.er['worker'].claim(experiment,'LIVE',task_id=larger['id'])
        self.assertEqual(lease['task_id'],larger['id'])
        self.assertEqual(lease['question_version_id'],larger['version'])
        self.assertEqual(self.state(smaller['id']),'QUEUED')
        self.assertEqual(self.attempts(experiment),1)
        other, _ = self.make_pair()
        self.assertEqual(self.er['worker'].claim(other,'LIVE')['task_id'],self.tasks(other)[0]['id'])

    def test_running_and_finished_target_never_fall_back_to_other_queued_task(self):
        experiment, _ = self.make_pair(); smaller, larger = self.tasks(experiment)
        lease = self.er['worker'].claim(experiment,'LIVE',task_id=larger['id'])
        self.assertIsNone(self.er['worker'].claim(experiment,'LIVE',task_id=larger['id']))
        self.er['worker'].fail(lease,'SYNTHETIC_BEFORE_SEND_FAILURE',0,False)
        self.assertIsNone(self.er['worker'].claim(experiment,'LIVE',task_id=larger['id']))
        self.assertEqual(self.state(smaller['id']),'QUEUED')
        self.assertEqual(self.state(larger['id']),'FAILED')
        self.assertEqual(self.attempts(experiment),1)

    def test_wrong_experiment_entry_or_missing_target_is_denied_without_attempts(self):
        experiment, _ = self.make_pair(models=2)
        other, _ = self.make_pair()
        targets = [self.tasks(other)[0]['id'], self.tasks(experiment,'model-1')[0]['id'], str(uuid4())]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.er['worker'].claim(experiment,'LIVE',task_id=target)
        self.assertEqual(self.attempts(experiment),0)
        self.assertEqual(self.attempts(other),0)
        self.assertEqual(self.r['operator'].summary(experiment)['reserved_microusd'],0)

    def test_runner_authentication_precedes_evidence_and_budget_lock(self):
        version, _ = self.question()
        experiment = self.r['operator'].register('exact-auth-'+uuid4().hex, [version], entries(), experiment_spec())
        self.r['operator'].bind_worker(experiment,'model-0',self.logins['worker'])
        target = self.tasks(experiment)[0]['id']
        # A FIXTURE has no LIVE evidence seal. A wrong LIVE runner must still get
        # 42501, not the evidence-state 55000; both public signatures share it.
        for options in ({}, {'task_id':target}):
            with self.subTest(options=options), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.er['worker'].claim(experiment,'LIVE',**options)

        # Neither a mismatched runner nor an unbound login may even wait on a
        # held budget lock. A short lock timeout would expose reordered checks.
        with psycopg.connect(base.previous.previous.DSN) as admin:
            admin.execute('SELECT 1 FROM fi.budget_accounts WHERE experiment_id=%s FOR UPDATE',(experiment,))
            for role, kind in (('worker','LIVE'), ('worker2','FIXTURE')):
                for exact in (False,True):
                    with self.subTest(role=role,exact=exact), psycopg.connect(self.dsns[role]) as worker:
                        worker.execute("SET LOCAL lock_timeout='100ms'")
                        statement = 'SELECT fi.claim_task(%s,%s,%s)' if exact else 'SELECT fi.claim_task(%s,%s)'
                        parameters = (experiment,kind,target) if exact else (experiment,kind)
                        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                            worker.execute(statement,parameters)
        self.assertEqual(self.attempts(experiment),0)
        self.assertEqual(self.r['operator'].summary(experiment)['reserved_microusd'],0)

    def test_null_exact_selector_and_private_helper_are_not_bypasses(self):
        experiment, _ = self.make_pair(); target = self.tasks(experiment)[0]['id']
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.call('worker','SELECT fi.claim_task(%s,%s,NULL::uuid)',(experiment,'LIVE'))
        for function in ('claim_task_selected','claim_task_p002','claim_task_before_admission'):
            statement = ('SELECT fi.'+function+'(%s,%s,%s)' if function=='claim_task_selected'
                         else 'SELECT fi.'+function+'(%s,%s)')
            parameters = (experiment,'LIVE',target) if function=='claim_task_selected' else (experiment,'LIVE')
            with self.subTest(function=function), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.call('worker',statement,parameters)
        self.assertEqual(self.attempts(experiment),0)

    def test_sealed_evidence_and_explicit_operation_policy_remain_required(self):
        for options in ({'sealed':False},{'mode':None}):
            experiment, _ = self.make_pair(**options); target = self.tasks(experiment)[0]['id']
            with self.subTest(options=options), self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                self.er['worker'].claim(experiment,'LIVE',task_id=target)
            self.assertEqual(self.attempts(experiment),0)

    def test_pause_blocks_exact_claim_until_explicit_resume(self):
        experiment, _ = self.make_pair(); target = self.tasks(experiment)[0]['id']
        self.call('operator','SELECT fi.set_operation_pause(%s,true,%s)',(experiment,'Synthetic pause before exact claim'))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].claim(experiment,'LIVE',task_id=target)
        self.assertEqual(self.attempts(experiment),0)
        self.call('operator','SELECT fi.set_operation_pause(%s,false,%s)',(experiment,'Synthetic resume before exact claim'))
        self.assertEqual(self.er['worker'].claim(experiment,'LIVE',task_id=target)['task_id'],target)

    def test_unknown_reservation_blocks_next_exact_claim_at_budget_cap(self):
        experiment, _ = self.make_pair(budget_microusd=1000000)
        first, second = self.tasks(experiment)
        wire = fixture.FakeWire(fixture.WireResponse(401,b'SYNTHETIC AUTH FAILURE'))
        result = run_once(self.er['worker'],experiment,wire,{'OPENAI_API_KEY':'synthetic'},task_id=first['id'])
        self.assertEqual(result['status'],'HTTP_ERROR'); self.assertEqual(wire.calls,1)
        self.assertEqual(self.state(first['id']),'UNCERTAIN')
        self.assertEqual(self.admin('SELECT state FROM fi.budget_reservations WHERE attempt_id=%s',
                                    (result['attempt_id'],)),'HELD_UNKNOWN')
        self.assertIsNone(self.er['worker'].claim(experiment,'LIVE',task_id=second['id']))
        self.assertEqual(self.state(second['id']),'QUEUED'); self.assertEqual(self.attempts(experiment),1)

    def test_revoked_source_fails_only_selected_task_without_an_attempt(self):
        experiment, permission = self.make_pair(); smaller, larger = self.tasks(experiment)
        self.call('source','SELECT fi.revoke_source_permission(%s,%s)',(permission,'Synthetic source revocation'))
        self.assertIsNone(self.er['worker'].claim(experiment,'LIVE',task_id=larger['id']))
        self.assertEqual(self.state(larger['id']),'FAILED'); self.assertEqual(self.state(smaller['id']),'QUEUED')
        self.assertEqual(self.attempts(experiment),0)

    def test_formal_exact_claim_requires_anchor_and_retains_dispatch_pause_gate(self):
        experiment, _, _, _ = self.registered(); target = self.tasks(experiment)[0]['id']
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].claim(experiment,'LIVE',task_id=target)
        self.assertEqual(self.attempts(experiment),0)
        anchored, _, _, _ = self.anchored(); target = self.tasks(anchored)[0]['id']
        lease = self.er['worker'].claim(anchored,'LIVE',task_id=target)
        body = fixture.build_request(lease['entry'],self.er['worker'].context(lease))
        self.call('operator','SELECT fi.set_operation_pause(%s,true,%s)',(anchored,'Synthetic pause after exact claim'))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.er['worker'].prepare(lease,body)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',
                                    (lease['attempt_id'],)),0)

    def test_concurrent_exact_claims_create_only_one_attempt(self):
        experiment, _ = self.make_pair(); smaller, larger = self.tasks(experiment)
        with ThreadPoolExecutor(2) as pool:
            leases = list(pool.map(lambda _:self.er['worker'].claim(experiment,'LIVE',task_id=larger['id']),range(2)))
        self.assertEqual(sum(lease is not None for lease in leases),1)
        self.assertEqual(self.attempts(experiment),1); self.assertEqual(self.state(smaller['id']),'QUEUED')
        self.assertEqual(self.r['operator'].summary(experiment)['reserved_microusd'],1000000)

    def test_expired_exact_lease_preserves_recovery_fencing(self):
        experiment, _ = self.make_pair(lease_seconds=1); target = self.tasks(experiment)[-1]['id']
        old = self.er['worker'].claim(experiment,'LIVE',task_id=target)
        time.sleep(1.1)
        self.assertEqual(self.r['operator'].recover(experiment),1)
        current = self.er['worker'].claim(experiment,'LIVE',task_id=target)
        self.assertGreater(current['fence'],old['fence'])
        with self.assertRaises(psycopg.Error): self.er['worker'].heartbeat(old)
        self.assertEqual(current['task_id'],target)
