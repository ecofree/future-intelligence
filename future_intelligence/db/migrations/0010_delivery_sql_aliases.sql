-- Forward correction after real PostgreSQL integration exposed an uninitialized record alias.
-- Preserve already applied migrations 0007-0009.

CREATE OR REPLACE FUNCTION fi.capture_batch(e uuid,k text,plan jsonb DEFAULT NULL,profile jsonb DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; old fi.commitment_batches; reg fi.commitment_batches;
  bid uuid:=gen_random_uuid(); task_row record; txt text; n integer:=0; questions jsonb; c jsonb;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF k IS NULL OR k NOT IN ('REGISTRATION','FORECAST') THEN RAISE EXCEPTION 'invalid batch kind' USING ERRCODE='22023'; END IF;
  SELECT * INTO old FROM fi.commitment_batches WHERE experiment_id=e AND kind=k;
  IF FOUND THEN
    IF k='REGISTRATION' AND (old.analysis_plan IS DISTINCT FROM plan OR old.trust_profile IS DISTINCT FROM profile) THEN
      RAISE EXCEPTION 'batch configuration conflict' USING ERRCODE='22023'; END IF;
    RETURN old.id;
  END IF;
  IF k='REGISTRATION' THEN
    PERFORM fi.validate_delivery_plan(plan,profile);
    IF clock_timestamp()>=x.deadline OR EXISTS(SELECT 1 FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=e) THEN
      RAISE EXCEPTION 'registration must precede attempts and deadline' USING ERRCODE='55000'; END IF;
    IF x.spec->>'run_kind'='LIVE' AND ((profile->>'fixture')::bool OR NOT EXISTS(SELECT 1 FROM fi.evidence_seals WHERE experiment_id=e)) THEN
      RAISE EXCEPTION 'LIVE requires sealed evidence and production trust' USING ERRCODE='55000'; END IF;
    FOR c IN SELECT * FROM jsonb_array_elements(plan->'comparisons') LOOP
      IF jsonb_typeof(c)<>'array' OR jsonb_array_length(c)<>2 OR c->>0=c->>1
        OR (SELECT count(*) FROM fi.experiment_entries WHERE experiment_id=e AND entry_key IN (c->>0,c->>1))<>2 THEN
        RAISE EXCEPTION 'comparison must reference two registered entries' USING ERRCODE='22023'; END IF;
    END LOOP;
  ELSE
    SELECT * INTO STRICT reg FROM fi.commitment_batches WHERE experiment_id=e AND kind='REGISTRATION';
    IF NOT EXISTS(SELECT 1 FROM fi.batch_seals WHERE batch_id=reg.id) THEN RAISE EXCEPTION 'registration not sealed' USING ERRCODE='55000'; END IF;
    plan:=reg.analysis_plan;profile:=reg.trust_profile;
  END IF;
  INSERT INTO fi.commitment_batches(id,experiment_id,kind,analysis_plan,trust_profile) VALUES(bid,e,k,plan,profile);
  SELECT jsonb_agg(jsonb_build_object('version_id',v.id,'snapshot_text',v.snapshot_text,'snapshot_sha256',v.snapshot_sha256) ORDER BY v.id)
    INTO questions FROM fi.question_versions v WHERE v.id IN(SELECT question_version_id FROM fi.expected_tasks WHERE experiment_id=e);
  txt:=jsonb_build_object('record_type','MANIFEST','experiment_id',e,'run_kind',x.spec->>'run_kind','suite',x.spec->>'suite',
    'experiment_manifest',x.manifest_text,'evidence_manifest',(SELECT manifest_text FROM fi.evidence_seals WHERE experiment_id=e),
    'questions',questions,'analysis_plan',plan,'trust_profile',profile,
    'task_count',(SELECT count(*) FROM fi.expected_tasks WHERE experiment_id=e))::text;
  IF k='FORECAST' THEN
    txt:=(txt::jsonb||jsonb_build_object('first_attempt_at',(SELECT min(a.claimed_at) FROM fi.task_attempts a JOIN fi.expected_tasks q ON q.id=a.task_id WHERE q.experiment_id=e)))::text;
  END IF;
  INSERT INTO fi.batch_records VALUES(bid,0,txt);
  FOR task_row IN SELECT q.*,cfg.config_sha256,st.status,sub.snapshot_text,pr.request_sha256,po.observation_sha256
      FROM fi.expected_tasks q JOIN fi.experiment_entries cfg ON cfg.experiment_id=e AND cfg.entry_key=q.entry_key
      JOIN fi.task_states st ON st.task_id=q.id LEFT JOIN fi.task_submissions sub ON sub.task_id=q.id
      LEFT JOIN fi.provider_requests pr ON pr.attempt_id=sub.attempt_id LEFT JOIN fi.provider_observations po ON po.attempt_id=sub.attempt_id
      WHERE q.experiment_id=e ORDER BY q.id LOOP
    n:=n+1;
    c:=jsonb_build_object('record_type','TASK','task_id',task_row.id,'question_version_id',task_row.question_version_id,
      'entry_key',task_row.entry_key,'entry_config_sha256',task_row.config_sha256);
    IF k='FORECAST' THEN c:=c||jsonb_build_object('status_at_seal',task_row.status,'submission',task_row.snapshot_text::jsonb,
      'provider_request_sha256',task_row.request_sha256,'provider_observation_sha256',task_row.observation_sha256); END IF;
    INSERT INTO fi.batch_records VALUES(bid,n,c::text);
  END LOOP;
  PERFORM fi.ledger_emit(e,NULL,'BATCH_CAPTURED',jsonb_build_object('batch_id',bid,'kind',k,'task_count',n));
  RETURN bid;
END $$;

CREATE OR REPLACE FUNCTION fi.add_outcome_evidence(v uuid,p uuid,key text,published timestamptz,revision int,val text,sha text,ref text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE q fi.question_versions; perm fi.source_permissions; old fi.outcome_evidence; eid uuid; moment timestamptz;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT q FROM fi.question_versions WHERE id=v;
  SELECT * INTO STRICT perm FROM fi.source_permissions WHERE id=p;
  PERFORM 1 FROM fi.sources WHERE id=perm.source_id FOR SHARE;
  PERFORM 1 FROM fi.question_versions WHERE id=v FOR UPDATE;
  moment:=clock_timestamp();
  IF NOT EXISTS(SELECT 1 FROM fi.question_sources WHERE version_id=v AND source_id=perm.source_id)
    OR NOT(perm.allowed_uses @> ARRAY['internal_research','storage','model_evaluation']::text[])
    OR moment<perm.valid_from OR moment>=perm.valid_until
    OR EXISTS(SELECT 1 FROM fi.permission_revocations WHERE permission_id=p)
    OR EXISTS(SELECT 1 FROM fi.source_permissions WHERE source_id=perm.source_id AND source_permissions.revision>perm.revision) THEN
    RAISE EXCEPTION 'result source not licensed' USING ERRCODE='42501'; END IF;
  IF key IS NULL OR length(btrim(key)) NOT BETWEEN 1 AND 200 OR ref IS NULL OR length(btrim(ref)) NOT BETWEEN 1 AND 2000
    OR sha IS NULL OR sha!~'^[0-9a-f]{64}$' OR published IS NULL OR revision IS NULL OR revision<0
    OR val IS NULL OR length(val)>100 OR val!~'^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    OR (q.spec->>'question_type'='binary' AND val NOT IN ('0','1')) THEN
    RAISE EXCEPTION 'invalid result evidence' USING ERRCODE='22023'; END IF;
  IF moment<q.outcome_not_before OR published<q.outcome_not_before OR published>q.resolution_deadline OR published>moment THEN
    RAISE EXCEPTION 'result outside registered observation window' USING ERRCODE='55000'; END IF;
  SELECT * INTO old FROM fi.outcome_evidence WHERE version_id=v AND source_id=perm.source_id AND release_key=key;
  IF FOUND THEN
    IF old.value_text<>val OR old.document_sha256<>sha OR old.published_at<>published OR old.revision_order<>revision THEN
      RAISE EXCEPTION 'release revision conflict; register a distinct official revision' USING ERRCODE='22023'; END IF; RETURN old.id;
  END IF;
  INSERT INTO fi.outcome_evidence(version_id,source_id,permission_id,release_key,published_at,revision_order,value_text,document_sha256,source_reference)
    VALUES(v,perm.source_id,p,key,published,revision,val,sha,ref) RETURNING id INTO eid;
  PERFORM fi.audit(v,'OUTCOME_EVIDENCE_CAPTURED',jsonb_build_object('evidence_id',eid,'acquisition','OPERATOR_VERIFIED_RELEASE'));
  RETURN eid;
END $$;

CREATE OR REPLACE FUNCTION fi.score_experiment(e uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; b fi.batch_catalog; reg fi.batch_catalog; ctx text; hash text; run uuid; row record;
  task jsonb; sub jsonb; probs jsonb; outcome_row fi.outcome_catalog; first_claim timestamptz;
  ok_time boolean; ps numeric; base numeric; loss text; bins int; y int; i int; cdf numeric; target numeric; uniform_cdf numeric;
  primary_val numeric; state_text text; metric_text text; cohort text;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  FOR row IN SELECT DISTINCT question_version_id FROM fi.expected_tasks WHERE experiment_id=e ORDER BY question_version_id LOOP
    PERFORM 1 FROM fi.question_versions WHERE id=row.question_version_id FOR SHARE;
  END LOOP;
  SELECT * INTO STRICT b FROM fi.batch_catalog WHERE experiment_id=e AND kind='FORECAST';
  SELECT * INTO STRICT reg FROM fi.batch_catalog WHERE experiment_id=e AND kind='REGISTRATION';
  IF b.subject_sha256 IS NULL OR reg.subject_sha256 IS NULL THEN RAISE EXCEPTION 'both complete batches must be sealed' USING ERRCODE='55000'; END IF;
  SELECT min(a.claimed_at) INTO first_claim FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=e;
  ok_time:=coalesce(x.spec->>'run_kind'='LIVE' AND NOT(reg.trust_profile->>'fixture')::bool
    AND reg.verified_upper_time<=first_claim AND b.verified_upper_time<=x.deadline,false);
  SELECT jsonb_build_object('algorithm','fi.score.v1','experiment_id',e,'run_kind',x.spec->>'run_kind',
    'registration_subject',reg.subject_sha256,'forecast_subject',b.subject_sha256,'eligible_time',ok_time,
    'registration_upper_time',reg.verified_upper_time,'forecast_upper_time',b.verified_upper_time,'first_claim',first_claim,
    'outcomes',jsonb_agg(to_jsonb(o) ORDER BY o.version_id))::text INTO ctx FROM fi.outcome_catalog o
    WHERE o.version_id IN(SELECT question_version_id FROM fi.expected_tasks WHERE experiment_id=e);
  hash:=encode(sha256(convert_to(ctx,'UTF8')),'hex');
  SELECT id INTO run FROM fi.score_runs WHERE experiment_id=e AND algorithm='fi.score.v1' AND context_sha256=hash;
  IF FOUND THEN RETURN run; END IF;
  INSERT INTO fi.score_runs(experiment_id,algorithm,context_text,context_sha256) VALUES(e,'fi.score.v1',ctx,hash) RETURNING id INTO run;
  FOR row IN SELECT record_text FROM fi.batch_records WHERE batch_id=b.id AND ordinal>0 ORDER BY ordinal LOOP
    task:=row.record_text::jsonb;sub:=task->'submission';
    SELECT * INTO STRICT outcome_row FROM fi.outcome_catalog WHERE version_id=(task->>'question_version_id')::uuid;
    metric_text:=CASE WHEN outcome_row.spec->>'question_type'='binary' THEN 'brier' ELSE 'normalized_rps' END;
    cohort:=encode(sha256(convert_to(jsonb_build_object('suite',x.spec->>'suite','run_kind',x.spec->>'run_kind',
      'domain',outcome_row.spec->>'domain','track',outcome_row.spec->>'track','question_type',outcome_row.spec->>'question_type','bins',outcome_row.spec->'bin_edges','unit',outcome_row.spec->>'unit')::text,'UTF8')),'hex');
    ps:=NULL;base:=NULL;loss:=NULL;primary_val:=NULL;state_text:=outcome_row.status;
    IF outcome_row.status='RESOLVED' THEN
      bins:=CASE WHEN metric_text='brier' THEN 2 ELSE jsonb_array_length(outcome_row.spec->'bin_edges')+1 END;
      IF bins=2 AND metric_text='brier' THEN y:=outcome_row.value_text::integer;
      ELSE SELECT count(*) INTO y FROM jsonb_array_elements_text(outcome_row.spec->'bin_edges') z WHERE outcome_row.value_text::numeric>=z::numeric; END IF;
      base:=0;
      FOR i IN 0..bins-2 LOOP
        uniform_cdf:=((1000000/bins)*(i+1)+least(i+1,1000000%bins))::numeric/1000000;
        target:=CASE WHEN y<=i THEN 1 ELSE 0 END;
        base:=base+power(uniform_cdf-target,2)/(bins-1);
      END LOOP;
      IF sub IS NULL OR sub='null'::jsonb THEN state_text:='MISSING_PREDICTION';
      ELSE
        probs:=sub->'probabilities_ppm';cdf:=0;ps:=0;
        FOR i IN 0..bins-2 LOOP
          cdf:=cdf+(probs->>i)::numeric/1000000;target:=CASE WHEN y<=i THEN 1 ELSE 0 END;
          ps:=ps+power(cdf-target,2)/(bins-1);
        END LOOP;
        IF metric_text='brier' THEN
          loss:=CASE WHEN (probs->>y)::numeric=0 THEN 'Infinity' ELSE (-ln((probs->>y)::numeric/1000000))::text END;
        END IF;
        IF ok_time THEN primary_val:=ps;state_text:='TIMED_LIVE';
        ELSIF x.spec->>'run_kind'='FIXTURE' THEN state_text:='FIXTURE_DIAGNOSTIC';
        ELSE state_text:='UNVERIFIED_TIME'; END IF;
      END IF;
    END IF;
    INSERT INTO fi.score_rows VALUES(run,(task->>'task_id')::uuid,task->>'entry_key',outcome_row.version_id,outcome_row.resolution_id,
      outcome_row.event_cluster,cohort,metric_text,state_text,primary_val,ps,base,loss);
  END LOOP;
  PERFORM fi.audit(e,'SCORE_RUN_CREATED',jsonb_build_object('run_id',run,'context_sha256',hash));RETURN run;
END $$;
