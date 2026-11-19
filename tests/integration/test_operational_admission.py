"""Disposable PostgreSQL. All documents, models and TSA are synthetic tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import test_live_evidence as previous
from tsa_fixture import fixture_tsa, fixture_crl, timestamp
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.delivery.timestamps import CRLTrustProfile

PLAN=dict(protocol='fi.analysis.v1',seed=73,resamples=200,alpha_ppm=50000,
          min_clusters=20,comparisons=[],stopping_rule='FIXED_REGISTERED_BATCH')


class OperationalAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous.LiveEvidenceTests.setUpClass.__func__(cls)
        cls.tmp=tempfile.TemporaryDirectory()
        cls.ca,cls.tsa=fixture_tsa(Path(cls.tmp.name))
        # Production-shaped policy only to test gates. This CA is NOT independent.
        cls.profile=CRLTrustProfile(hashlib.sha256(cls.ca).hexdigest(),('1.3.6.1.4.1.55555.1',),fixture=False,
            revocation_policy='offline-full-chain-crl-v1',max_crl_age_seconds=3600)
        cls.crl=fixture_crl(Path(cls.tmp.name))
        cls.profile_json=json.loads(json.dumps(asdict(cls.profile)))
        login='fi_admission_verifier_'+uuid4().hex[:8];password=uuid4().hex
        with psycopg.connect(previous.previous.DSN) as c:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT PASSWORD {}').format(sql.Identifier(login),sql.Literal(password)))
            c.execute(sql.SQL('GRANT fi_verifier TO {}').format(sql.Identifier(login)))
        cls.logins['verifier']=login
        cls.dsns['verifier']=make_conninfo(previous.previous.DSN,user=login,password=password)
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        previous.LiveEvidenceTests.tearDownClass.__func__(cls)
    call=previous.LiveEvidenceTests.call
    admin=previous.LiveEvidenceTests.admin
    question=previous.LiveEvidenceTests.question
    make=previous.LiveEvidenceTests.make
    claim=previous.LiveEvidenceTests.claim
    prepare=previous.LiveEvidenceTests.prepare

    def checks(self,e,v,p,finding='NOT_FOUND'):
        ids=[]
        for kind,result in [('CALENDAR','REVIEWED'),('TARGET_STATUS',finding)]:
            ids.append(str(self.call('source','SELECT fi.add_admission_check(%s,%s,%s,%s,%s,%s,%s,%s)',
              (e,v,p,kind,result,'https://example.invalid/ledger',b'SYNTHETIC REVIEW DOCUMENT '+kind.encode(),
               'TEST fixture review, not an actual official publication'))))
        return ids

    def confirm(self,e,v,ids,role='reviewer',cluster=None):
        cluster=cluster or self.admin('SELECT event_cluster FROM fi.question_versions WHERE id=%s',(v,))
        return self.call(role,'SELECT fi.confirm_admission(%s,%s,%s::uuid[],%s,%s)',
                         (e,v,ids,cluster,'Independent TEST review of all sources and the release grouping'))

    def registered(self):
        e,v,p,_=self.make(operation_mode='FORMAL');ids=self.checks(e,v,p);self.confirm(e,v,ids)
        repo=DeliveryRepository(self.dsns['operator'])
        bid=repo.capture(e,'REGISTRATION',PLAN,self.profile_json);repo.seal(bid)
        return e,v,p,bid

    def anchored(self):
        e,v,p,bid=self.registered();repo=DeliveryRepository(self.dsns['operator'])
        rid,q=repo.new_anchor(bid);repo.receive(rid,timestamp(self.tsa,q))
        report=DeliveryRepository(self.dsns['verifier']).verify_anchor(rid,self.ca,self.profile,crl_bundle=self.crl)
        upper=datetime.fromisoformat(report['upper_time'])
        time.sleep(max(0,(upper-datetime.now(timezone.utc)).total_seconds())+.02)
        return e,v,p,bid

    def test_no_implicit_diagnostic_or_formal_mode(self):
        e,*_=self.make(operation_mode=None)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState): self.claim(e)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=%s',(e,)),0)

    def test_diagnostic_cannot_capture_formal_batch(self):
        e,*_=self.make()
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            DeliveryRepository(self.dsns['operator']).capture(e,'REGISTRATION',PLAN,self.profile_json)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.commitment_batches WHERE experiment_id=%s',(e,)),0)

    def test_policy_immutable_and_worker_not_operator(self):
        e,*_=self.make()
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',(e,'FORMAL',5,3600,'Cannot change a registered diagnostic mode'))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',(e,'FORMAL',5,3600,'Worker cannot create policies'))

    def test_full_admission_required_before_registration(self):
        e,v,p,_=self.make(operation_mode='FORMAL');self.checks(e,v,p)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            DeliveryRepository(self.dsns['operator']).capture(e,'REGISTRATION',PLAN,self.profile_json)

    def test_known_or_uncertain_blocks_admission(self):
        for finding in ('KNOWN','UNCERTAIN'):
            e,v,p,_=self.make(operation_mode='FORMAL');ids=self.checks(e,v,p,finding)
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.confirm(e,v,ids)

    def test_wrong_cluster_cannot_be_reviewed(self):
        e,v,p,_=self.make(operation_mode='FORMAL');ids=self.checks(e,v,p)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.confirm(e,v,ids,cluster='unrelated-cluster')

    def test_independent_reviewer_even_if_operator_has_role(self):
        e,v,p,_=self.make(operation_mode='FORMAL');ids=self.checks(e,v,p)
        with psycopg.connect(previous.previous.DSN) as c:
            c.execute(sql.SQL('GRANT fi_reviewer TO {}').format(sql.Identifier(self.logins['operator'])))
        try:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.confirm(e,v,ids,role='operator')
        finally:
            with psycopg.connect(previous.previous.DSN) as c:
                c.execute(sql.SQL('REVOKE fi_reviewer FROM {}').format(sql.Identifier(self.logins['operator'])))

    def test_snapshot_hash_and_manifest_binding(self):
        e,v,p,bid=self.registered()
        record=self.admin('SELECT record_text FROM fi.batch_records WHERE batch_id=%s AND ordinal=0',(bid,))
        obj=json.loads(record)
        self.assertEqual(obj['admission_protocol'],'fi.admission.v1')
        self.assertEqual(obj['operation_policy']['mode'],'FORMAL')
        self.assertEqual(len(obj['admission_confirmations']),1)
        checks=obj['admission_confirmations'][0]['checks'];self.assertEqual(len(checks),2)
        for check in checks:
            raw=self.admin('SELECT document FROM fi.admission_checks WHERE id=%s',(check['id'],))
            self.assertEqual(hashlib.sha256(bytes(raw)).hexdigest(),check['document_sha256'])

    def test_old_claim_entry_requires_registration_anchor(self):
        e,_,_,_=self.registered()
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.claim(e)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('worker','SELECT fi.claim_task_before_admission(%s,%s)',(e,'LIVE'))

    def test_anchored_formal_can_prepare_without_external_send(self):
        e,*_=self.anchored();lease,body=self.prepare(e)
        self.assertIn('model',body)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),1)

    def test_pause_after_claim_blocks_old_prepare(self):
        e,*_=self.anchored();lease=self.claim(e)
        body=previous.fixture.build_request(lease['entry'],self.er['worker'].context(lease))
        self.call('operator','SELECT fi.set_operation_pause(%s,true,%s)',(e,'TEST manual pause after claim'))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.er['worker'].prepare(lease,body)
        self.assertEqual(self.admin('SELECT count(*) FROM fi.provider_requests WHERE attempt_id=%s',(lease['attempt_id'],)),0)
        self.call('operator','SELECT fi.set_operation_pause(%s,false,%s)',(e,'TEST manual resume before send'))
        self.assertTrue(self.er['worker'].prepare(lease,body))

    def test_known_answer_after_claim_blocks_dispatch(self):
        e,v,p,_=self.anchored();lease=self.claim(e)
        body=previous.fixture.build_request(lease['entry'],self.er['worker'].context(lease))
        self.checks(e,v,p,'KNOWN')
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.er['worker'].prepare(lease,body)

    def test_known_answer_queued_retains_failed_denominator(self):
        e,v,p,_=self.anchored();self.checks(e,v,p,'KNOWN')
        self.assertIsNone(self.claim(e))
        self.assertEqual(self.r['operator'].summary(e)['total_tasks'],1)
        self.assertEqual(self.admin('SELECT status FROM fi.task_states st JOIN fi.expected_tasks t ON t.id=st.task_id WHERE t.experiment_id=%s',(e,)),'FAILED')

    def test_pause_commits_during_lock_wait(self):
        e,*_=self.anchored()
        with psycopg.connect(self.dsns['operator']) as c,ThreadPoolExecutor() as pool:
            c.execute('SELECT fi.set_operation_pause(%s,true,%s)',(e,'TEST pause held until concurrent claim waits'))
            pending=pool.submit(self.claim,e);time.sleep(.1);self.assertFalse(pending.done());c.commit()
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):pending.result(timeout=5)

    def test_worker_cannot_read_snapshot_or_change_pause(self):
        e,*_=self.make()
        for table in ('operation_policies','admission_checks','admission_confirmations'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.call('worker','SELECT count(*) FROM fi.'+table)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.call('worker','SELECT fi.set_operation_pause(%s,true,%s)',(e,'not allowed for the worker'))

    def test_audit_records_cannot_be_mutated(self):
        e,v,p,_=self.make(operation_mode='FORMAL');ids=self.checks(e,v,p);self.confirm(e,v,ids)
        for table in ('operation_policies','admission_checks','admission_confirmations'):
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                self.admin('DELETE FROM fi.'+table+' WHERE experiment_id=%s RETURNING experiment_id',(e,))

    def test_expired_snapshot_rejected_without_rewriting_history(self):
        e,v,p,_=self.make(operation_mode='FORMAL')
        ids=self.checks(e,v,p)
        # Insert a historical synthetic snapshot as fixture owner; no mutation of
        # any previously written evidence, and no client-provided time in API.
        old=self.admin('''INSERT INTO fi.admission_checks(experiment_id,version_id,permission_id,kind,finding,
           source_reference,document,document_sha256,note,checked_at,submitted_by)
           SELECT experiment_id,version_id,permission_id,kind,finding,source_reference,document,
             document_sha256,note,clock_timestamp()-interval '2 hours',submitted_by FROM fi.admission_checks
           WHERE id=%s RETURNING id''',(ids[0],))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.confirm(e,v,[str(old),ids[1]])

    def test_seal_window_reserves_time_for_proof(self):
        e,*_=self.make(operation_mode=None)
        # The one-hour reserve reaches this fixture's one-hour deadline.
        self.call('operator','SELECT fi.configure_operation(%s,%s,%s,%s,%s)',
                  (e,'DIAGNOSTIC',3600,3600,'Synthetic reserve-boundary test before any calls'))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.claim(e)

    def test_duplicate_checks_and_foreign_question_rejected(self):
        e,v,p,_=self.make(operation_mode='FORMAL');ids=self.checks(e,v,p)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):self.confirm(e,v,[ids[0],ids[0]])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.call('source','SELECT fi.add_admission_check(%s,%s,%s,%s,%s,%s,%s,%s)',
                      (e,str(uuid4()),p,'TARGET_STATUS','NOT_FOUND','https://example.invalid/',b'TEST','Unregistered version must be rejected'))

    def test_uncertain_later_snapshot_not_cleared_by_good_snapshot(self):
        e,v,p,_=self.anchored();self.checks(e,v,p,'UNCERTAIN');self.checks(e,v,p,'NOT_FOUND')
        self.assertIsNone(self.claim(e))
        error=self.admin('SELECT st.last_error FROM fi.task_states st JOIN fi.expected_tasks t ON t.id=st.task_id WHERE t.experiment_id=%s',(e,))
        self.assertEqual(error,'TARGET_KNOWN_OR_UNCERTAIN')

    def test_formal_manifest_pair_independently_verifies(self):
        from future_intelligence.delivery.batch import verify_pair
        e,_,_,bid=self.anchored();repo=DeliveryRepository(self.dsns['operator'])
        registration=repo.seal(bid)
        final=repo.seal(repo.capture(e,'FORECAST'))
        verify_pair(registration,final)

    def test_operation_status_contains_no_raw_evidence(self):
        e,v,p,_=self.make(operation_mode='FORMAL');self.checks(e,v,p)
        state=self.call('operator','SELECT fi.operation_status(%s)',(e,))
        self.assertEqual(state['mode'],'FORMAL')
        self.assertNotIn('SYNTHETIC REVIEW DOCUMENT',json.dumps(state,default=str))
        self.assertEqual(state['held_unknown'],0)
