-- Exact task selection is opt-in. Both public claim signatures use the same
-- admission, evidence, budget and lease logic; the legacy UUID order is retained.
-- No applied migration or existing experiment/task record is rewritten.
CREATE FUNCTION fi.claim_task_selected(e uuid,p_kind text,p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; b fi.budget_accounts; t fi.expected_tasks; s fi.task_states;
  a uuid:=gen_random_uuid(); cap bigint; until_time timestamptz; cfg jsonb;
  candidate record; reason text; policy fi.operation_policies;
BEGIN
  PERFORM fi.ledger_rc();
  -- Keep 0006 authentication precedence: do not lock the budget or disclose
  -- evidence/admission state to a mismatched runner or unbound login.
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  IF p_kind IS DISTINCT FROM x.spec->>'run_kind' THEN
    RAISE EXCEPTION 'runner kind mismatch' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings WHERE experiment_id=e AND login=session_user) THEN
    RAISE EXCEPTION 'worker has no entry binding' USING ERRCODE='42501'; END IF;
  IF p_task IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM fi.expected_tasks q JOIN fi.worker_bindings w
      ON w.experiment_id=q.experiment_id AND w.entry_key=q.entry_key
    WHERE q.id=p_task AND q.experiment_id=e AND w.login=session_user) THEN
    RAISE EXCEPTION 'requested task is not bound to this worker and experiment' USING ERRCODE='42501';
  END IF;

  -- Preserve the operational-admission wrapper, including the locked recheck.
  IF x.spec->>'run_kind'='LIVE' THEN
    PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
    reason:=fi.operation_reason(e);
    IF reason IS NOT NULL THEN RAISE EXCEPTION '%',reason USING ERRCODE='55000'; END IF;
    SELECT * INTO STRICT policy FROM fi.operation_policies WHERE experiment_id=e;
    IF policy.mode='FORMAL' THEN
      IF NOT EXISTS(SELECT 1 FROM fi.batch_catalog z WHERE z.experiment_id=e AND z.kind='REGISTRATION'
          AND z.verified_upper_time<=clock_timestamp() AND z.trust_profile->'fixture'='false'::jsonb) THEN
        RAISE EXCEPTION 'REGISTRATION_TIME_NOT_VERIFIED' USING ERRCODE='55000'; END IF;
      FOR candidate IN SELECT q.id,q.question_version_id FROM fi.expected_tasks q
        JOIN fi.worker_bindings w ON w.experiment_id=e AND w.entry_key=q.entry_key AND w.login=session_user
        JOIN fi.task_states st ON st.task_id=q.id
        WHERE q.experiment_id=e AND st.status='QUEUED' AND (p_task IS NULL OR q.id=p_task)
        ORDER BY q.id LOOP
        reason:=fi.operation_reason(e,candidate.question_version_id);
        IF reason IS NOT NULL THEN
          UPDATE fi.task_states SET status='FAILED',last_error=reason,completed_at=clock_timestamp() WHERE task_id=candidate.id;
          PERFORM fi.ledger_emit(e,candidate.id,'ADMISSION_EXCLUDED',jsonb_build_object('reason',reason));
        END IF;
      END LOOP;
    END IF;
  END IF;

  -- Preserve the evidence wrapper before any attempt or reservation exists.
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF x.spec->>'run_kind'='LIVE' AND NOT EXISTS(SELECT 1 FROM fi.evidence_seals WHERE experiment_id=e) THEN
    RAISE EXCEPTION 'LIVE requires sealed evidence' USING ERRCODE='55000'; END IF;

  SELECT * INTO STRICT b FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF clock_timestamp()>=x.deadline THEN RETURN NULL; END IF;
  cap:=(x.spec->>'attempt_cap_microusd')::bigint;
  IF b.spent_microusd+b.reserved_microusd+cap>(x.spec->>'budget_microusd')::bigint THEN RETURN NULL; END IF;
  LOOP
    SELECT st.* INTO s FROM fi.task_states st JOIN fi.expected_tasks q ON q.id=st.task_id
      JOIN fi.worker_bindings w ON w.experiment_id=q.experiment_id AND w.entry_key=q.entry_key
      WHERE q.experiment_id=e AND w.login=session_user AND st.status='QUEUED'
        AND st.attempt_no<(x.spec->>'max_attempts')::integer
        AND (p_task IS NULL OR q.id=p_task)
      ORDER BY st.task_id FOR UPDATE OF st SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT * INTO STRICT t FROM fi.expected_tasks WHERE id=s.task_id;
    BEGIN
      PERFORM fi.assert_sources(t.question_version_id);
    EXCEPTION WHEN insufficient_privilege THEN
      UPDATE fi.task_states SET status='FAILED',last_error='SOURCE_PERMISSION',completed_at=clock_timestamp() WHERE task_id=t.id;
      PERFORM fi.ledger_emit(e,t.id,'TASK_FAILED','{"reason":"SOURCE_PERMISSION"}');
      CONTINUE;
    END;
    IF clock_timestamp()>=x.deadline THEN RETURN NULL; END IF;
    until_time:=least(x.deadline,clock_timestamp()+make_interval(secs=>(x.spec->>'lease_seconds')::integer));
    INSERT INTO fi.task_attempts(id,task_id,ordinal,fence,state)
      VALUES(a,t.id,s.attempt_no+1,s.fence+1,'CLAIMED');
    INSERT INTO fi.budget_reservations(attempt_id,reserved_microusd) VALUES(a,cap);
    UPDATE fi.budget_accounts SET reserved_microusd=reserved_microusd+cap WHERE experiment_id=e;
    UPDATE fi.task_states SET status='RUNNING',attempt_no=attempt_no+1,fence=fence+1,
      active_attempt=a,owner=session_user,lease_until=until_time,last_error=NULL WHERE task_id=t.id;
    PERFORM fi.ledger_emit(e,t.id,'TASK_CLAIMED',jsonb_build_object('attempt_id',a,'fence',s.fence+1));
    SELECT config INTO cfg FROM fi.experiment_entries WHERE experiment_id=e AND entry_key=t.entry_key;
    RETURN jsonb_build_object('task_id',t.id,'attempt_id',a,'fence',s.fence+1,'lease_until',until_time,
      'question_version_id',t.question_version_id,'entry',cfg,'run_kind',x.spec->>'run_kind','cap_microusd',cap,
      'question_spec',(SELECT spec FROM fi.question_versions WHERE id=t.question_version_id));
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION fi.claim_task(e uuid,kind text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  RETURN fi.claim_task_selected(e,kind,NULL);
END $$;

CREATE FUNCTION fi.claim_task(e uuid,kind text,target uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  IF target IS NULL THEN RAISE EXCEPTION 'exact task id required' USING ERRCODE='22023'; END IF;
  RETURN fi.claim_task_selected(e,kind,target);
END $$;

REVOKE ALL ON FUNCTION fi.claim_task_selected(uuid,text,uuid) FROM PUBLIC,fi_worker;
REVOKE ALL ON FUNCTION fi.claim_task(uuid,text),fi.claim_task(uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.claim_task(uuid,text),fi.claim_task(uuid,text,uuid) TO fi_worker;
