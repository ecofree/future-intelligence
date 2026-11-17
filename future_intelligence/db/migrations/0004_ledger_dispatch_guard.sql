-- Forward hardening: a completed request may report cost above its reservation.
-- Already claimed but undispatched work must not start while the account is over
-- budget. This is NOT a guarantee of vendor pricing or exactly-once billing.
CREATE OR REPLACE FUNCTION fi.start_task_call(t uuid,a uuid,f bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE q fi.expected_tasks;
BEGIN
  PERFORM fi.ledger_active(t,a,f);
  SELECT * INTO q FROM fi.expected_tasks WHERE id=t;
  PERFORM fi.assert_sources(q.question_version_id);
  PERFORM fi.ledger_active(t,a,f);
  IF EXISTS(SELECT 1 FROM fi.task_attempts WHERE id=a AND call_started_at IS NOT NULL) THEN
    RETURN false;
  END IF;
  IF EXISTS(SELECT 1 FROM fi.budget_accounts b JOIN fi.experiments e ON e.id=b.experiment_id
    WHERE b.experiment_id=q.experiment_id
      AND b.spent_microusd+b.reserved_microusd>(e.spec->>'budget_microusd')::bigint) THEN
    RAISE EXCEPTION 'budget overrun blocks dispatch' USING ERRCODE='55000';
  END IF;
  UPDATE fi.task_attempts SET state='CALL_STARTED',call_started_at=clock_timestamp() WHERE id=a;
  PERFORM fi.ledger_emit(q.experiment_id,t,'CALL_STARTED',jsonb_build_object('attempt_id',a));
  RETURN true;
END $$;
REVOKE ALL ON FUNCTION fi.start_task_call(uuid,uuid,bigint) FROM PUBLIC;
