-- Forward-only boundary correction; no historical records or checksums change.
-- Predictions and their complete time proofs must be strictly before cutoff.
-- A true publication at cutoff is admissible: outcome window endpoints include equality.
-- Do not move an official publication timestamp by one microsecond to satisfy a schema.
DO $boundary_check$
DECLARE old_check name;
BEGIN
  -- Locate the original check by its exact expression, not its generated name.
  -- Missing, duplicated or altered checks fail closed; other checks are untouched.
  SELECT c.conname INTO STRICT old_check FROM pg_constraint c
    WHERE c.conrelid='fi.question_versions'::regclass AND c.contype='c' AND c.convalidated
      AND lower(regexp_replace(pg_get_expr(c.conbin,c.conrelid),'[[:space:]()]','','g'))
        ='forecast_deadline<outcome_not_beforeandoutcome_not_before<=resolution_deadline';
  EXECUTE format('ALTER TABLE fi.question_versions DROP CONSTRAINT %I',old_check);
EXCEPTION WHEN no_data_found OR too_many_rows THEN
  RAISE EXCEPTION 'expected exactly one original question time-window constraint' USING ERRCODE='55000';
END $boundary_check$;
ALTER TABLE fi.question_versions ADD CONSTRAINT question_versions_time_window_check
  CHECK (forecast_deadline <= outcome_not_before AND outcome_not_before <= resolution_deadline);

CREATE OR REPLACE FUNCTION fi.validate_spec(s jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE k text; v jsonb; edge jsonb; n numeric; previous numeric;
  required text[] := ARRAY['title','event_definition','domain','track','question_type','event_cluster',
    'original_timezone','forecast_deadline','outcome_not_before','resolution_deadline',
    'resolution_rule','data_revision_policy','unit','bin_edges'];
  deadline timestamptz; outcome_time timestamptz; resolution_time timestamptz;
BEGIN
  IF s IS NULL OR jsonb_typeof(s) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'spec must be object' USING ERRCODE='22023'; END IF;
  IF NOT (s ?& required) OR (SELECT count(*) FROM jsonb_object_keys(s)) <> cardinality(required) THEN
    RAISE EXCEPTION 'spec keys do not match v1' USING ERRCODE='22023'; END IF;
  FOR k,v IN SELECT * FROM jsonb_each(s) LOOP
    IF k <> 'bin_edges' AND (jsonb_typeof(v) <> 'string' OR length(btrim(s->>k)) NOT BETWEEN 1 AND 10000) THEN
      RAISE EXCEPTION 'invalid string field: %', k USING ERRCODE='22023'; END IF;
  END LOOP;
  IF s->>'domain' NOT IN ('MACRO','POLICY') OR s->>'track' NOT IN ('FAST','STRATEGIC')
    OR s->>'question_type' NOT IN ('binary','binned_numeric')
    OR s->>'data_revision_policy' NOT IN ('FIRST_RELEASE','LATEST_AT_RESOLUTION_DEADLINE') THEN
    RAISE EXCEPTION 'unsupported classification or revision policy' USING ERRCODE='22023'; END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name = s->>'original_timezone') THEN
    RAISE EXCEPTION 'unknown original timezone' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['forecast_deadline','outcome_not_before','resolution_deadline'] LOOP
    IF s->>k !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' THEN
      RAISE EXCEPTION 'timestamp must be ISO8601 with offset: %', k USING ERRCODE='22023'; END IF;
  END LOOP;
  deadline := (s->>'forecast_deadline')::timestamptz;
  outcome_time := (s->>'outcome_not_before')::timestamptz;
  resolution_time := (s->>'resolution_deadline')::timestamptz;
  IF NOT (deadline <= outcome_time AND outcome_time <= resolution_time) THEN
    RAISE EXCEPTION 'invalid deadline ordering' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(s->'bin_edges') <> 'array' THEN
    RAISE EXCEPTION 'bin_edges must be array' USING ERRCODE='22023'; END IF;
  IF jsonb_array_length(s->'bin_edges') > 100
    OR (s->>'question_type'='binary' AND jsonb_array_length(s->'bin_edges')<>0)
    OR (s->>'question_type'='binned_numeric' AND jsonb_array_length(s->'bin_edges')=0) THEN
    RAISE EXCEPTION 'invalid bin count' USING ERRCODE='22023'; END IF;
  FOR edge IN SELECT * FROM jsonb_array_elements(s->'bin_edges') LOOP
    IF jsonb_typeof(edge) <> 'string' OR length(edge#>>'{}') > 100
      OR edge#>>'{}' !~ '^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$' THEN
      RAISE EXCEPTION 'boundary must be finite decimal string' USING ERRCODE='22023'; END IF;
    n := (edge#>>'{}')::numeric;
    IF previous IS NOT NULL AND n <= previous THEN
      RAISE EXCEPTION 'boundaries must strictly increase' USING ERRCODE='22023'; END IF;
    previous := n;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION fi.attest_anchor(r uuid,result jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE req fi.anchor_requests; rep fi.anchor_replies; b fi.commitment_batches;
  s fi.batch_seals; deadline timestamptz; upper_at timestamptz; gen_at timestamptz; old fi.anchor_verifications;
BEGIN
  SELECT * INTO STRICT req FROM fi.anchor_requests WHERE id=r FOR UPDATE;
  SELECT * INTO STRICT rep FROM fi.anchor_replies WHERE request_id=r;
  SELECT * INTO STRICT b FROM fi.commitment_batches WHERE id=req.batch_id;
  SELECT * INTO STRICT s FROM fi.batch_seals WHERE batch_id=b.id;
  SELECT e.deadline INTO deadline FROM fi.experiments e WHERE e.id=b.experiment_id;
  IF req.protocol<>'RFC3161' OR rep.status<>'RECEIVED' OR result IS NULL OR
    result->>'protocol' IS DISTINCT FROM 'fi.rfc3161-verification.v1' OR result->>'subject_sha256' IS DISTINCT FROM s.subject_sha256
    OR result->>'ca_sha256' IS DISTINCT FROM b.trust_profile->>'ca_sha256'
    OR result->'fixture' IS DISTINCT FROM b.trust_profile->'fixture'
    OR result->>'reply_sha256' IS DISTINCT FROM encode(sha256(rep.reply_bytes),'hex')
    OR NOT(b.trust_profile->'policy_oids' ? (result->>'policy_oid')) THEN
    RAISE EXCEPTION 'verification does not match registered trust and proof' USING ERRCODE='22023'; END IF;
  upper_at:=(result->>'upper_time')::timestamptz;gen_at:=(result->>'gen_time')::timestamptz;
  IF upper_at IS NULL OR gen_at IS NULL OR upper_at<gen_at OR gen_at>rep.received_at+interval '5 seconds'
    OR result->>'status' IS DISTINCT FROM (CASE WHEN upper_at<deadline THEN 'VERIFIED' ELSE 'VERIFIED_LATE' END)
    OR (result->>'accuracy_us')::bigint>(b.trust_profile->>'max_accuracy_us')::bigint THEN
    RAISE EXCEPTION 'invalid timestamp time bounds' USING ERRCODE='22023'; END IF;
  SELECT * INTO old FROM fi.anchor_verifications WHERE request_id=r;
  IF FOUND THEN
    IF old.report IS DISTINCT FROM result THEN RAISE EXCEPTION 'verification conflict' USING ERRCODE='22023'; END IF; RETURN;
  END IF;
  INSERT INTO fi.anchor_verifications(request_id,status,upper_time,report) VALUES(r,result->>'status',upper_at,result);
  PERFORM fi.audit(b.id,'EXTERNAL_TIME_VERIFIED',jsonb_build_object('request_id',r,'status',result->>'status','fixture',result->'fixture'));
END $$;

-- Retain old immutable verification reports verbatim, including any old
-- VERIFIED-at-deadline rows; exclude those rows from current admission/score eligibility.
CREATE OR REPLACE VIEW fi.batch_catalog AS SELECT b.*,s.root_sha256,s.subject_sha256,s.sealed_at,
  (SELECT count(*) FROM fi.batch_records r WHERE r.batch_id=b.id) AS record_count,
  (SELECT min(v.upper_time) FROM fi.anchor_requests a JOIN fi.anchor_verifications v ON v.request_id=a.id
    WHERE a.batch_id=b.id AND v.status='VERIFIED' AND v.upper_time<x.deadline) AS verified_upper_time
  FROM fi.commitment_batches b JOIN fi.experiments x ON x.id=b.experiment_id
    LEFT JOIN fi.batch_seals s ON s.batch_id=b.id;

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
    AND reg.verified_upper_time<=first_claim AND b.verified_upper_time<x.deadline,false);
  SELECT jsonb_build_object('algorithm','fi.score.v2','experiment_id',e,'run_kind',x.spec->>'run_kind',
    'registration_subject',reg.subject_sha256,'forecast_subject',b.subject_sha256,'eligible_time',ok_time,
    'registration_upper_time',reg.verified_upper_time,'forecast_upper_time',b.verified_upper_time,'first_claim',first_claim,
    'outcomes',jsonb_agg(to_jsonb(o) ORDER BY o.version_id))::text INTO ctx FROM fi.outcome_catalog o
    WHERE o.version_id IN(SELECT question_version_id FROM fi.expected_tasks WHERE experiment_id=e);
  hash:=encode(sha256(convert_to(ctx,'UTF8')),'hex');
  SELECT id INTO run FROM fi.score_runs WHERE experiment_id=e AND algorithm='fi.score.v2' AND context_sha256=hash;
  IF FOUND THEN RETURN run; END IF;
  INSERT INTO fi.score_runs(experiment_id,algorithm,context_text,context_sha256) VALUES(e,'fi.score.v2',ctx,hash) RETURNING id INTO run;
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
