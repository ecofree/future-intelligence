-- Forward only. Existing records remain historical; new LIVE calls need an
-- explicit mode. DIAGNOSTIC permits API qualification, never timed-live scoring.
CREATE TABLE fi.operation_policies (
  experiment_id uuid PRIMARY KEY REFERENCES fi.experiments,
  mode text NOT NULL CHECK (mode IN ('FORMAL','DIAGNOSTIC')),
  seal_margin_seconds integer NOT NULL CHECK (seal_margin_seconds BETWEEN 5 AND 3600),
  check_max_age_seconds integer NOT NULL CHECK (check_max_age_seconds BETWEEN 60 AND 86400),
  note text NOT NULL CHECK (length(btrim(note)) BETWEEN 10 AND 2000),
  created_by name NOT NULL DEFAULT session_user,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.admission_checks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES fi.operation_policies,
  version_id uuid NOT NULL REFERENCES fi.question_versions,
  permission_id uuid NOT NULL REFERENCES fi.source_permissions,
  kind text NOT NULL CHECK (kind IN ('CALENDAR','TARGET_STATUS')),
  finding text NOT NULL CHECK (finding IN ('REVIEWED','NOT_FOUND','KNOWN','UNCERTAIN')),
  source_reference text NOT NULL CHECK (source_reference ~ '^https://[^/@[:space:]]+(/[^[:space:]]*)?$'),
  document bytea NOT NULL CHECK (octet_length(document) BETWEEN 1 AND 2000000),
  document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
  note text NOT NULL CHECK (length(btrim(note)) BETWEEN 10 AND 2000),
  checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  submitted_by name NOT NULL DEFAULT session_user,
  CHECK ((kind='CALENDAR' AND finding IN ('REVIEWED','UNCERTAIN'))
      OR (kind='TARGET_STATUS' AND finding IN ('NOT_FOUND','KNOWN','UNCERTAIN')))
);
CREATE INDEX admission_checks_version ON fi.admission_checks(experiment_id,version_id);
CREATE TABLE fi.admission_confirmations (
  experiment_id uuid NOT NULL REFERENCES fi.operation_policies,
  version_id uuid NOT NULL REFERENCES fi.question_versions,
  check_ids uuid[] NOT NULL,
  event_cluster text NOT NULL,
  note text NOT NULL CHECK (length(btrim(note)) BETWEEN 10 AND 2000),
  reviewed_by name NOT NULL DEFAULT session_user,
  reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (experiment_id,version_id)
);
CREATE TABLE fi.operation_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id uuid NOT NULL REFERENCES fi.operation_policies,
  kind text NOT NULL CHECK(kind IN ('PAUSE','RESUME')),
  note text NOT NULL CHECK(length(btrim(note)) BETWEEN 10 AND 2000),
  actor name NOT NULL DEFAULT session_user,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION fi.configure_operation(e uuid,m text,margin integer,age integer,n text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE old fi.operation_policies;
BEGIN
  PERFORM fi.ledger_rc();
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown experiment' USING ERRCODE='22023'; END IF;
  SELECT * INTO old FROM fi.operation_policies WHERE experiment_id=e;
  IF FOUND THEN
    IF old.mode IS DISTINCT FROM m OR old.seal_margin_seconds IS DISTINCT FROM margin
      OR old.check_max_age_seconds IS DISTINCT FROM age OR old.note IS DISTINCT FROM n THEN
      RAISE EXCEPTION 'operation policy immutable' USING ERRCODE='55000'; END IF;
    RETURN;
  END IF;
  IF EXISTS(SELECT 1 FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=e)
     OR EXISTS(SELECT 1 FROM fi.commitment_batches b WHERE b.experiment_id=e) THEN
    RAISE EXCEPTION 'configure before attempts and batches' USING ERRCODE='55000'; END IF;
  INSERT INTO fi.operation_policies(experiment_id,mode,seal_margin_seconds,check_max_age_seconds,note)
    VALUES(e,m,margin,age,n);
  PERFORM fi.audit(e,'OPERATION_CONFIGURED',jsonb_build_object('mode',m,'seal_margin_seconds',margin));
END $$;

CREATE FUNCTION fi.add_admission_check(e uuid,v uuid,p uuid,k text,f text,ref text,body bytea,n text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE result uuid;
BEGIN
  PERFORM fi.ledger_rc();
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM fi.expected_tasks t WHERE t.experiment_id=e AND t.question_version_id=v)
    OR NOT EXISTS(SELECT 1 FROM fi.question_sources qs WHERE qs.version_id=v AND qs.permission_id=p) THEN
    RAISE EXCEPTION 'check must use registered question and permission' USING ERRCODE='42501'; END IF;
  PERFORM fi.assert_sources(v);
  INSERT INTO fi.admission_checks(experiment_id,version_id,permission_id,kind,finding,source_reference,document,document_sha256,note)
    VALUES(e,v,p,k,f,ref,body,encode(sha256(body),'hex'),n) RETURNING id INTO result;
  PERFORM fi.audit(e,'ADMISSION_CHECK_RECORDED',jsonb_build_object('check_id',result,'finding',f));
  RETURN result;
END $$;

CREATE FUNCTION fi.confirm_admission(e uuid,v uuid,ids uuid[],cluster text,n text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE policy fi.operation_policies; question fi.question_versions; now_at timestamptz;
BEGIN
  PERFORM fi.ledger_rc();
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  SELECT * INTO STRICT policy FROM fi.operation_policies WHERE experiment_id=e;
  SELECT * INTO STRICT question FROM fi.question_versions WHERE id=v;
  now_at:=clock_timestamp();
  IF policy.created_by=session_user OR pg_has_role(session_user,'fi_operator','MEMBER')
    OR pg_has_role(session_user,'fi_worker','MEMBER')
    OR EXISTS(SELECT 1 FROM fi.admission_checks c WHERE c.id=ANY(ids) AND c.submitted_by=session_user) THEN
    RAISE EXCEPTION 'independent admission reviewer required' USING ERRCODE='42501'; END IF;
  IF policy.mode<>'FORMAL' OR now_at>=question.forecast_deadline
    OR EXISTS(SELECT 1 FROM fi.commitment_batches b WHERE b.experiment_id=e)
    OR ids IS NULL OR cardinality(ids) NOT BETWEEN 2 AND 20 OR array_position(ids,NULL) IS NOT NULL
    OR cardinality(ids)<>(SELECT count(DISTINCT i) FROM unnest(ids) i)
    OR cluster IS DISTINCT FROM question.event_cluster THEN
    RAISE EXCEPTION 'invalid or late admission' USING ERRCODE='55000'; END IF;
  PERFORM fi.assert_sources(v);
  IF (SELECT count(*) FROM fi.admission_checks c WHERE c.id=ANY(ids) AND c.experiment_id=e AND c.version_id=v
        AND c.checked_at>now_at-policy.check_max_age_seconds*interval '1 second')<>cardinality(ids)
    OR NOT EXISTS(SELECT 1 FROM fi.admission_checks c WHERE c.id=ANY(ids) AND c.kind='CALENDAR' AND c.finding='REVIEWED')
    OR NOT EXISTS(SELECT 1 FROM fi.admission_checks c WHERE c.id=ANY(ids) AND c.kind='TARGET_STATUS' AND c.finding='NOT_FOUND')
    OR EXISTS(SELECT 1 FROM fi.admission_checks c WHERE c.experiment_id=e AND c.version_id=v AND c.finding IN ('KNOWN','UNCERTAIN')) THEN
    RAISE EXCEPTION 'missing stale known or uncertain admission evidence' USING ERRCODE='55000'; END IF;
  INSERT INTO fi.admission_confirmations(experiment_id,version_id,check_ids,event_cluster,note)
    VALUES(e,v,ids,cluster,n);
  PERFORM fi.audit(e,'ADMISSION_CONFIRMED',jsonb_build_object('version_id',v,'check_ids',ids));
END $$;

-- Shared read routine, not public: checks are evaluated under the experiment
-- budget lock. Check insertion and pause use the same lock, including lock waits.
CREATE FUNCTION fi.operation_reason(e uuid,v uuid DEFAULT NULL) RETURNS text
LANGUAGE plpgsql SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE policy fi.operation_policies; cutoff timestamptz; now_at timestamptz;
BEGIN
  SELECT * INTO policy FROM fi.operation_policies WHERE experiment_id=e;
  IF NOT FOUND THEN RETURN 'OPERATION_POLICY_REQUIRED'; END IF;
  SELECT deadline INTO STRICT cutoff FROM fi.experiments WHERE id=e;
  now_at:=clock_timestamp();
  IF now_at>=cutoff-policy.seal_margin_seconds*interval '1 second'
    OR EXISTS(SELECT 1 FROM fi.commitment_batches b WHERE b.experiment_id=e AND b.kind='FORECAST') THEN
    RETURN 'SEAL_WINDOW_OR_CLOSED'; END IF;
  IF (SELECT z.kind FROM fi.operation_events z WHERE z.experiment_id=e ORDER BY z.id DESC LIMIT 1)='PAUSE' THEN
    RETURN 'OPERATOR_PAUSED'; END IF;
  IF policy.mode='DIAGNOSTIC' OR v IS NULL THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM fi.expected_tasks t WHERE t.experiment_id=e AND (v IS NULL OR t.question_version_id=v)
    AND NOT EXISTS(SELECT 1 FROM fi.admission_confirmations a WHERE a.experiment_id=e AND a.version_id=t.question_version_id)) THEN
    RETURN 'ADMISSION_REVIEW_REQUIRED'; END IF;
  IF EXISTS(SELECT 1 FROM fi.admission_confirmations a JOIN fi.admission_checks c ON c.id=ANY(a.check_ids)
      WHERE a.experiment_id=e AND (v IS NULL OR a.version_id=v)
      AND c.checked_at<=now_at-policy.check_max_age_seconds*interval '1 second') THEN RETURN 'ADMISSION_EXPIRED'; END IF;
  IF EXISTS(SELECT 1 FROM fi.admission_checks c WHERE c.experiment_id=e AND (v IS NULL OR c.version_id=v)
      AND c.finding IN ('KNOWN','UNCERTAIN')) THEN RETURN 'TARGET_KNOWN_OR_UNCERTAIN'; END IF;
  RETURN NULL;
END $$;

CREATE FUNCTION fi.live_admission_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE task fi.expected_tasks; config jsonb; problem text; policy fi.operation_policies; cutoff timestamptz; run_kind text;
BEGIN
  SELECT * INTO STRICT task FROM fi.expected_tasks WHERE id=NEW.task_id;
  SELECT x.spec->>'run_kind',x.deadline INTO run_kind,cutoff FROM fi.experiments x WHERE x.id=task.experiment_id;
  IF run_kind<>'LIVE' THEN RETURN NEW; END IF;
  PERFORM fi.ledger_rc();
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=task.experiment_id FOR UPDATE;
  problem:=fi.operation_reason(task.experiment_id,task.question_version_id);
  IF problem IS NOT NULL THEN RAISE EXCEPTION '%',problem USING ERRCODE='55000'; END IF;
  SELECT * INTO STRICT policy FROM fi.operation_policies WHERE experiment_id=task.experiment_id;
  IF policy.mode='FORMAL' AND NOT EXISTS(SELECT 1 FROM fi.batch_catalog b WHERE b.experiment_id=task.experiment_id
      AND b.kind='REGISTRATION' AND b.verified_upper_time<=clock_timestamp()
      AND b.trust_profile->'fixture'='false'::jsonb) THEN
    RAISE EXCEPTION 'REGISTRATION_TIME_NOT_VERIFIED' USING ERRCODE='55000'; END IF;
  IF TG_TABLE_NAME='provider_requests' THEN
    SELECT x.config INTO config FROM fi.experiment_entries x WHERE x.experiment_id=task.experiment_id AND x.entry_key=task.entry_key;
    IF clock_timestamp()+((config->'parameters'->>'timeout_seconds')::int+10)*interval '1 second'
       >=cutoff-policy.seal_margin_seconds*interval '1 second' THEN
      RAISE EXCEPTION 'INSUFFICIENT_DISPATCH_WINDOW' USING ERRCODE='55000'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER operational_admission BEFORE INSERT ON fi.task_attempts FOR EACH ROW EXECUTE FUNCTION fi.live_admission_guard();
CREATE TRIGGER operational_admission BEFORE INSERT ON fi.provider_requests FOR EACH ROW EXECUTE FUNCTION fi.live_admission_guard();

-- Admission declarations are inside the same externally anchored manifest.
CREATE FUNCTION fi.bind_admission_manifest() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE e uuid; run_kind text; policy fi.operation_policies; problem text; admissions jsonb; target uuid;
BEGIN
  IF NEW.ordinal<>0 THEN RETURN NEW; END IF;
  e:=(NEW.record_text::jsonb->>'experiment_id')::uuid;
  SELECT x.spec->>'run_kind' INTO run_kind FROM fi.experiments x WHERE x.id=e;
  IF run_kind<>'LIVE' THEN RETURN NEW; END IF;
  SELECT * INTO policy FROM fi.operation_policies WHERE experiment_id=e;
  IF NOT FOUND OR policy.mode<>'FORMAL' THEN
    RAISE EXCEPTION 'DIAGNOSTIC cannot enter formal commitment batches' USING ERRCODE='55000'; END IF;
  IF (SELECT kind FROM fi.commitment_batches WHERE id=NEW.batch_id)='REGISTRATION' THEN
    problem:=fi.operation_reason(e);
    IF problem IS NOT NULL THEN RAISE EXCEPTION '%',problem USING ERRCODE='55000'; END IF;
    FOR target IN SELECT DISTINCT t.question_version_id FROM fi.expected_tasks t WHERE t.experiment_id=e LOOP
      problem:=fi.operation_reason(e,target);
      IF problem IS NOT NULL THEN RAISE EXCEPTION '%',problem USING ERRCODE='55000'; END IF;
    END LOOP;
  END IF;
  SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('checks',(
    SELECT jsonb_agg(jsonb_build_object('id',c.id,'kind',c.kind,'finding',c.finding,'source_reference',c.source_reference,
      'permission_id',c.permission_id,'document_sha256',c.document_sha256,'checked_at',c.checked_at,'submitted_by',c.submitted_by)
      ORDER BY c.id) FROM fi.admission_checks c WHERE c.id=ANY(a.check_ids))) ORDER BY a.version_id)
    INTO admissions FROM fi.admission_confirmations a WHERE a.experiment_id=e;
  NEW.record_text:=(NEW.record_text::jsonb||jsonb_build_object('admission_protocol','fi.admission.v1',
    'operation_policy',to_jsonb(policy),'admission_confirmations',admissions))::text;
  RETURN NEW;
END $$;
CREATE TRIGGER bind_admission BEFORE INSERT ON fi.batch_records FOR EACH ROW EXECUTE FUNCTION fi.bind_admission_manifest();

CREATE FUNCTION fi.set_operation_pause(e uuid,paused boolean,n text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  PERFORM fi.ledger_rc();
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF paused IS NULL THEN RAISE EXCEPTION 'pause boolean required' USING ERRCODE='22023'; END IF;
  INSERT INTO fi.operation_events(experiment_id,kind,note) VALUES(e,CASE WHEN paused THEN 'PAUSE' ELSE 'RESUME' END,n);
END $$;

CREATE OR REPLACE FUNCTION fi.worker_delivery_gate(e uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE reason text; policy fi.operation_policies; BEGIN
  PERFORM fi.ledger_rc();
  IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings w WHERE w.experiment_id=e AND w.login=session_user) THEN
    RAISE EXCEPTION 'worker binding required' USING ERRCODE='42501'; END IF;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  reason:=fi.operation_reason(e);
  SELECT * INTO policy FROM fi.operation_policies WHERE experiment_id=e;
  IF reason IS NULL AND policy.mode='FORMAL' AND NOT EXISTS(SELECT 1 FROM fi.batch_catalog b WHERE b.experiment_id=e
      AND b.kind='REGISTRATION' AND b.verified_upper_time<=clock_timestamp() AND b.trust_profile->'fixture'='false'::jsonb) THEN
    reason:='REGISTRATION_TIME_NOT_VERIFIED'; END IF;
  RETURN jsonb_build_object('ready',reason IS NULL,'reason',coalesce(reason,'ADMITTED_NOT_PRODUCTION_ACCEPTED'),'mode',policy.mode);
END $$;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['operation_policies','admission_checks','admission_confirmations','operation_events'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.%I FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('REVOKE ALL ON fi.%I FROM PUBLIC',t);
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION fi.configure_operation(uuid,text,integer,integer,text),
 fi.add_admission_check(uuid,uuid,uuid,text,text,text,bytea,text),fi.confirm_admission(uuid,uuid,uuid[],text,text),
 fi.operation_reason(uuid,uuid),fi.live_admission_guard(),fi.bind_admission_manifest(),fi.set_operation_pause(uuid,boolean,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.configure_operation(uuid,text,integer,integer,text),fi.set_operation_pause(uuid,boolean,text) TO fi_operator;
GRANT EXECUTE ON FUNCTION fi.add_admission_check(uuid,uuid,uuid,text,text,text,bytea,text) TO fi_source_admin;
GRANT EXECUTE ON FUNCTION fi.confirm_admission(uuid,uuid,uuid[],text,text) TO fi_reviewer;
GRANT SELECT ON fi.operation_policies,fi.admission_checks,fi.admission_confirmations,fi.operation_events TO fi_operator,fi_reviewer,fi_source_admin;

-- Isolate per-question admission failures rather than poisoning the first queued
-- task forever. This is part of the existing transactional claim, not a second
-- queue or controller. Undispatched exclusions have no fabricated prediction.
ALTER FUNCTION fi.claim_task(uuid,text) RENAME TO claim_task_before_admission;
REVOKE ALL ON FUNCTION fi.claim_task_before_admission(uuid,text) FROM PUBLIC,fi_worker;
CREATE FUNCTION fi.claim_task(e uuid,kind text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE candidate record; reason text; policy fi.operation_policies;
BEGIN
  PERFORM fi.ledger_rc();
  IF (SELECT x.spec->>'run_kind' FROM fi.experiments x WHERE x.id=e)='LIVE' AND kind='LIVE' THEN
    IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings w WHERE w.experiment_id=e AND w.login=session_user) THEN
      RAISE EXCEPTION 'worker binding required' USING ERRCODE='42501'; END IF;
    PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
    reason:=fi.operation_reason(e);
    IF reason IS NOT NULL THEN RAISE EXCEPTION '%',reason USING ERRCODE='55000'; END IF;
    SELECT * INTO STRICT policy FROM fi.operation_policies WHERE experiment_id=e;
    IF policy.mode='FORMAL' THEN
      IF NOT EXISTS(SELECT 1 FROM fi.batch_catalog b WHERE b.experiment_id=e AND b.kind='REGISTRATION'
          AND b.verified_upper_time<=clock_timestamp() AND b.trust_profile->'fixture'='false'::jsonb) THEN
        RAISE EXCEPTION 'REGISTRATION_TIME_NOT_VERIFIED' USING ERRCODE='55000'; END IF;
      FOR candidate IN SELECT t.id,t.question_version_id FROM fi.expected_tasks t
        JOIN fi.worker_bindings w ON w.experiment_id=e AND w.entry_key=t.entry_key AND w.login=session_user
        JOIN fi.task_states st ON st.task_id=t.id WHERE t.experiment_id=e AND st.status='QUEUED' ORDER BY t.id LOOP
        reason:=fi.operation_reason(e,candidate.question_version_id);
        IF reason IS NOT NULL THEN
          UPDATE fi.task_states SET status='FAILED',last_error=reason,completed_at=clock_timestamp() WHERE task_id=candidate.id;
          PERFORM fi.ledger_emit(e,candidate.id,'ADMISSION_EXCLUDED',jsonb_build_object('reason',reason));
        END IF;
      END LOOP;
    END IF;
  END IF;
  RETURN fi.claim_task_before_admission(e,kind);
END $$;
REVOKE ALL ON FUNCTION fi.claim_task(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.claim_task(uuid,text) TO fi_worker;

CREATE FUNCTION fi.operation_status(e uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
 SELECT jsonb_build_object('experiment_id',x.id,'mode',p.mode,'deadline',x.deadline,
   'dispatch_stop_at',x.deadline-p.seal_margin_seconds*interval '1 second',
   'reason',fi.operation_reason(e),'registration_batch',(SELECT b.id FROM fi.commitment_batches b WHERE b.experiment_id=e AND b.kind='REGISTRATION'),
   'forecast_batch',(SELECT b.id FROM fi.commitment_batches b WHERE b.experiment_id=e AND b.kind='FORECAST'),
   'spent_microusd',a.spent_microusd,'reserved_microusd',a.reserved_microusd,
   'held_unknown',(SELECT count(*) FROM fi.budget_reservations br JOIN fi.task_attempts att ON att.id=br.attempt_id
      JOIN fi.expected_tasks t ON t.id=att.task_id WHERE t.experiment_id=e AND br.state='HELD_UNKNOWN'),
   'budget_microusd',x.spec->'budget_microusd',
   'states',(SELECT jsonb_object_agg(z.status,z.n) FROM (SELECT st.status,count(*) n FROM fi.expected_tasks t
       JOIN fi.task_states st ON st.task_id=t.id WHERE t.experiment_id=e GROUP BY st.status) z))
 FROM fi.experiments x LEFT JOIN fi.operation_policies p ON p.experiment_id=x.id
 JOIN fi.budget_accounts a ON a.experiment_id=x.id WHERE x.id=e;
$$;
REVOKE ALL ON FUNCTION fi.operation_status(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.operation_status(uuid) TO fi_operator,fi_dashboard;
