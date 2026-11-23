-- Forward-only CRL admission. Historical v1 proofs, reports, score runs and
-- applied migration files remain unchanged. No network calls occur in SQL.
-- Cryptographic verification belongs to the isolated verifier; SQL binds its
-- bounded signed material and recorded check to the frozen trust policy.
CREATE OR REPLACE FUNCTION fi.validate_delivery_plan(plan jsonb,profile jsonb) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE k text; n integer;
  profile_keys text[]:=ARRAY['ca_sha256','policy_oids','max_accuracy_us','missing_accuracy_us','fixture'];
BEGIN
  IF plan IS NULL OR jsonb_typeof(plan)<>'object' OR NOT(plan ?& ARRAY['protocol','seed','resamples','alpha_ppm','min_clusters','comparisons','stopping_rule'])
    OR (SELECT count(*) FROM jsonb_object_keys(plan))<>7
    OR plan->>'protocol' IS DISTINCT FROM 'fi.analysis.v1' OR plan->>'stopping_rule' IS DISTINCT FROM 'FIXED_REGISTERED_BATCH'
    OR jsonb_typeof(plan->'comparisons') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'invalid preregistered analysis plan' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['seed','resamples','alpha_ppm','min_clusters'] LOOP
    IF jsonb_typeof(plan->k) IS DISTINCT FROM 'number' OR (plan->>k)!~'^[0-9]{1,9}$' THEN
      RAISE EXCEPTION 'invalid analysis integer' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF (plan->>'resamples')::int NOT BETWEEN 200 AND 10000 OR (plan->>'alpha_ppm')::int NOT BETWEEN 1 AND 499999
    OR (plan->>'min_clusters')::int NOT BETWEEN 2 AND 10000 OR jsonb_array_length(plan->'comparisons')>45 THEN
    RAISE EXCEPTION 'analysis limits exceeded' USING ERRCODE='22023'; END IF;
  IF profile IS NULL OR jsonb_typeof(profile) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid timestamp trust profile' USING ERRCODE='22023'; END IF;
  IF profile ? 'revocation_policy' OR profile ? 'max_crl_age_seconds' THEN
    profile_keys:=profile_keys||ARRAY['revocation_policy','max_crl_age_seconds'];
    IF profile->>'revocation_policy' IS DISTINCT FROM 'offline-full-chain-crl-v1'
      OR jsonb_typeof(profile->'max_crl_age_seconds') IS DISTINCT FROM 'number'
      OR coalesce(profile->>'max_crl_age_seconds','')!~'^[0-9]{1,8}$' THEN
      RAISE EXCEPTION 'invalid CRL trust policy' USING ERRCODE='22023'; END IF;
    IF (profile->>'max_crl_age_seconds')::bigint NOT BETWEEN 1 AND 31536000 THEN
      RAISE EXCEPTION 'invalid CRL age bound' USING ERRCODE='22023'; END IF;
  END IF;
  IF NOT(profile ?& profile_keys)
    OR (SELECT count(*) FROM jsonb_object_keys(profile))<>cardinality(profile_keys)
    OR jsonb_typeof(profile->'ca_sha256') IS DISTINCT FROM 'string' OR (profile->>'ca_sha256')!~'^[0-9a-f]{64}$'
    OR jsonb_typeof(profile->'policy_oids') IS DISTINCT FROM 'array'
    OR jsonb_typeof(profile->'fixture') IS DISTINCT FROM 'boolean' THEN
    RAISE EXCEPTION 'invalid timestamp trust profile' USING ERRCODE='22023'; END IF;
  IF jsonb_array_length(profile->'policy_oids') NOT BETWEEN 1 AND 10
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(profile->'policy_oids') p WHERE jsonb_typeof(p)<>'string' OR p#>>'{}' !~ '^[0-2](\.[0-9]+)+$') THEN
    RAISE EXCEPTION 'TSA policies required' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['max_accuracy_us','missing_accuracy_us'] LOOP
    IF k='missing_accuracy_us' AND profile->k='null'::jsonb THEN CONTINUE; END IF;
    IF jsonb_typeof(profile->k) IS DISTINCT FROM 'number' OR (profile->>k)!~'^[0-9]{1,8}$'
      OR (profile->>k)::int>60000000 THEN RAISE EXCEPTION 'invalid accuracy bound' USING ERRCODE='22023'; END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION fi.verification_report_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x jsonb:=NEW.report; k text; g timestamptz; u timestamptz;
  valid_from timestamptz; valid_until timestamptz; crl bytea; pem text;
  fields text[]:=ARRAY['protocol','status','subject_sha256','gen_time','upper_time','accuracy_us',
    'policy_oid','ca_sha256','reply_sha256','fixture','verifier','revocation_status'];
  times text[]:=ARRAY['gen_time','upper_time']; version_two boolean;
BEGIN
  PERFORM fi.ledger_rc();
  IF pg_has_role(session_user,'fi_operator','MEMBER') OR pg_has_role(session_user,'fi_worker','MEMBER')
    OR pg_has_role(session_user,'fi_resolver','MEMBER') THEN
    RAISE EXCEPTION 'verifier requires isolated login' USING ERRCODE='42501'; END IF;
  IF x IS NULL OR jsonb_typeof(x) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'incomplete timestamp verification report' USING ERRCODE='22023'; END IF;
  version_two:=x->>'protocol' IS NOT DISTINCT FROM 'fi.rfc3161-verification.v2';
  IF version_two THEN
    fields:=fields||ARRAY['crl_sha256','crl_base64','crl_valid_from','crl_valid_until'];
    times:=times||ARRAY['crl_valid_from','crl_valid_until'];
  END IF;
  IF NOT(x ?& fields) OR (SELECT count(*) FROM jsonb_object_keys(x))<>cardinality(fields)
    OR octet_length(x::text)>3000000 THEN
    RAISE EXCEPTION 'incomplete timestamp verification report' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY fields LOOP
    IF k IN('accuracy_us','fixture','crl_base64') THEN CONTINUE; END IF;
    IF jsonb_typeof(x->k) IS DISTINCT FROM 'string' OR length(btrim(x->>k)) NOT BETWEEN 1 AND 500 THEN
      RAISE EXCEPTION 'invalid verification field' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF jsonb_typeof(x->'accuracy_us') IS DISTINCT FROM 'number' OR coalesce(x->>'accuracy_us','')!~'^[0-9]{1,8}$'
    OR jsonb_typeof(x->'fixture') IS DISTINCT FROM 'boolean' THEN
    RAISE EXCEPTION 'unsupported verification report contract' USING ERRCODE='22023'; END IF;
  IF (x->>'accuracy_us')::bigint>60000000 THEN
    RAISE EXCEPTION 'unsupported verification report contract' USING ERRCODE='22023'; END IF;
  IF version_two THEN
    IF x->>'verifier' IS DISTINCT FROM 'openssl-ts+fi-tstinfo-crl-v2'
      OR x->>'revocation_status' IS DISTINCT FROM 'GOOD'
      OR jsonb_typeof(x->'crl_base64') IS DISTINCT FROM 'string'
      OR coalesce(x->>'crl_sha256','')!~'^[0-9a-f]{64}$'
      OR length(x->>'crl_base64') NOT BETWEEN 1 AND 2666668 THEN
      RAISE EXCEPTION 'invalid CRL verification report' USING ERRCODE='22023'; END IF;
    BEGIN
      crl:=decode(x->>'crl_base64','base64');
      pem:=convert_from(crl,'UTF8');
    EXCEPTION WHEN data_exception THEN
      RAISE EXCEPTION 'invalid CRL encoding' USING ERRCODE='22023';
    END;
    IF octet_length(crl) NOT BETWEEN 1 AND 2000000
      OR encode(sha256(crl),'hex') IS DISTINCT FROM x->>'crl_sha256'
      OR replace(encode(crl,'base64'),E'\n','') IS DISTINCT FROM x->>'crl_base64'
      OR (length(pem)-length(replace(pem,'-----BEGIN X509 CRL-----','')))/length('-----BEGIN X509 CRL-----') NOT BETWEEN 1 AND 8
      OR pem!~'^[[:space:]]*-----BEGIN X509 CRL-----[A-Za-z0-9+/=[:space:]]+-----END X509 CRL-----([[:space:]]+-----BEGIN X509 CRL-----[A-Za-z0-9+/=[:space:]]+-----END X509 CRL-----)*[[:space:]]*$' THEN
      RAISE EXCEPTION 'invalid bounded CRL material' USING ERRCODE='22023'; END IF;
  ELSIF x->>'protocol' IS DISTINCT FROM 'fi.rfc3161-verification.v1'
    OR x->>'verifier' IS DISTINCT FROM 'openssl-ts+fi-tstinfo-v1'
    OR x->>'revocation_status' IS DISTINCT FROM 'NOT_CHECKED' THEN
    RAISE EXCEPTION 'unsupported verification report contract' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY times LOOP
    IF coalesce(x->>k,'')!~'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' THEN
      RAISE EXCEPTION 'verification time requires explicit offset' USING ERRCODE='22023'; END IF;
  END LOOP;
  BEGIN
    g:=(x->>'gen_time')::timestamptz;u:=(x->>'upper_time')::timestamptz;
    IF version_two THEN
      valid_from:=(x->>'crl_valid_from')::timestamptz;valid_until:=(x->>'crl_valid_until')::timestamptz;
    END IF;
  EXCEPTION WHEN data_exception THEN
    RAISE EXCEPTION 'invalid verification time' USING ERRCODE='22023';
  END;
  IF u IS NULL OR g IS NULL OR u<>g+(x->>'accuracy_us')::bigint*interval '1 microsecond'
    OR NEW.upper_time IS DISTINCT FROM u THEN
    RAISE EXCEPTION 'verification uncertainty must match upper time' USING ERRCODE='22023'; END IF;
  IF version_two AND (valid_from IS NULL OR valid_until IS NULL OR valid_from>=valid_until
      OR NEW.verified_at IS NULL OR NEW.verified_at<valid_from OR NEW.verified_at>=valid_until) THEN
    RAISE EXCEPTION 'CRL must cover recorded verification time' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION fi.attest_anchor(r uuid,result jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE req fi.anchor_requests; rep fi.anchor_replies; b fi.commitment_batches;
  s fi.batch_seals; deadline timestamptz; upper_at timestamptz; gen_at timestamptz; old fi.anchor_verifications;
  valid_from timestamptz; valid_until timestamptz; moment timestamptz; version_two boolean;
BEGIN
  SELECT * INTO STRICT req FROM fi.anchor_requests WHERE id=r FOR UPDATE;
  SELECT * INTO STRICT rep FROM fi.anchor_replies WHERE request_id=r;
  SELECT * INTO STRICT b FROM fi.commitment_batches WHERE id=req.batch_id;
  SELECT * INTO STRICT s FROM fi.batch_seals WHERE batch_id=b.id;
  SELECT e.deadline INTO deadline FROM fi.experiments e WHERE e.id=b.experiment_id;
  IF result IS NULL OR jsonb_typeof(result) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid timestamp verification report' USING ERRCODE='22023'; END IF;
  version_two:=result->>'protocol' IS NOT DISTINCT FROM 'fi.rfc3161-verification.v2';
  IF version_two THEN
    IF (SELECT count(*) FROM jsonb_object_keys(b.trust_profile))<>7
      OR b.trust_profile->>'revocation_policy' IS DISTINCT FROM 'offline-full-chain-crl-v1'
      OR jsonb_typeof(b.trust_profile->'max_crl_age_seconds') IS DISTINCT FROM 'number'
      OR coalesce(b.trust_profile->>'max_crl_age_seconds','')!~'^[0-9]{1,8}$'
      OR result->>'revocation_status' IS DISTINCT FROM 'GOOD' THEN
      RAISE EXCEPTION 'CRL proof requires the frozen CRL trust profile' USING ERRCODE='22023'; END IF;
    IF (b.trust_profile->>'max_crl_age_seconds')::bigint NOT BETWEEN 1 AND 31536000 THEN
      RAISE EXCEPTION 'invalid frozen CRL age bound' USING ERRCODE='22023'; END IF;
    IF jsonb_typeof(result->'crl_valid_from') IS DISTINCT FROM 'string'
      OR jsonb_typeof(result->'crl_valid_until') IS DISTINCT FROM 'string'
      OR coalesce(result->>'crl_valid_from','')!~'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$'
      OR coalesce(result->>'crl_valid_until','')!~'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' THEN
      RAISE EXCEPTION 'CRL times require explicit offsets' USING ERRCODE='22023'; END IF;
    BEGIN
      valid_from:=(result->>'crl_valid_from')::timestamptz;valid_until:=(result->>'crl_valid_until')::timestamptz;
    EXCEPTION WHEN data_exception THEN
      RAISE EXCEPTION 'invalid CRL time window' USING ERRCODE='22023';
    END;
    moment:=clock_timestamp();
    IF valid_from IS NULL OR valid_until IS NULL OR valid_from>=valid_until
      OR moment<valid_from OR moment>=valid_until
      OR valid_until>valid_from+(b.trust_profile->>'max_crl_age_seconds')::bigint*interval '1 second' THEN
      RAISE EXCEPTION 'CRL evidence is not currently valid under the frozen age policy' USING ERRCODE='22023'; END IF;
  ELSIF result->>'protocol' IS DISTINCT FROM 'fi.rfc3161-verification.v1'
    OR (SELECT count(*) FROM jsonb_object_keys(b.trust_profile))<>5 THEN
    RAISE EXCEPTION 'verification protocol and frozen trust profile disagree' USING ERRCODE='22023'; END IF;
  IF req.protocol<>'RFC3161' OR rep.status<>'RECEIVED' OR result IS NULL OR
    result->>'subject_sha256' IS DISTINCT FROM s.subject_sha256
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

-- Shared immutable-evidence qualification. Current admission and historical
-- scoring use these same records but intentionally evaluate different times.
CREATE VIEW fi.anchor_time_catalog AS
  SELECT a.batch_id,v.request_id,v.upper_time,v.verified_at AS checked_at,
    (b.trust_profile->>'fixture')::boolean AS fixture,
    CASE WHEN v.report->>'protocol'='fi.rfc3161-verification.v2'
      THEN (v.report->>'crl_valid_from')::timestamptz END AS valid_from,
    CASE WHEN v.report->>'protocol'='fi.rfc3161-verification.v2'
      THEN (v.report->>'crl_valid_until')::timestamptz END AS valid_until,
    encode(sha256(convert_to(v.report::text,'UTF8')),'hex') AS verification_sha256
  FROM fi.anchor_requests a JOIN fi.anchor_verifications v ON v.request_id=a.id
    JOIN fi.commitment_batches b ON b.id=a.batch_id JOIN fi.experiments x ON x.id=b.experiment_id
  WHERE v.status='VERIFIED' AND v.upper_time<x.deadline
    AND v.report->'fixture'=b.trust_profile->'fixture'
    AND ((v.report->>'protocol'='fi.rfc3161-verification.v1'
      AND v.report->>'verifier'='openssl-ts+fi-tstinfo-v1' AND v.report->>'revocation_status'='NOT_CHECKED'
      AND b.trust_profile->'fixture'='true'::jsonb)
    OR (v.report->>'protocol'='fi.rfc3161-verification.v2'
      AND v.report->>'verifier'='openssl-ts+fi-tstinfo-crl-v2' AND v.report->>'revocation_status'='GOOD'
      AND b.trust_profile->>'revocation_policy'='offline-full-chain-crl-v1'
      AND (v.report->>'crl_valid_from')::timestamptz<=v.verified_at
      AND v.verified_at<(v.report->>'crl_valid_until')::timestamptz));
REVOKE ALL ON fi.anchor_time_catalog FROM PUBLIC,fi_worker,fi_operator,fi_verifier,fi_scorekeeper,fi_dashboard;

-- Existing FORMAL claim, prepare and worker gates all consume this one current
-- admission field. Legacy nonfixture NOT_CHECKED never contributes eligibility.
CREATE OR REPLACE VIEW fi.batch_catalog AS SELECT b.*,s.root_sha256,s.subject_sha256,s.sealed_at,
  (SELECT count(*) FROM fi.batch_records r WHERE r.batch_id=b.id) AS record_count,
  (SELECT min(v.upper_time) FROM fi.anchor_time_catalog v WHERE v.batch_id=b.id
    AND v.checked_at<=clock_timestamp()
    AND (v.valid_from IS NULL OR (v.valid_from<=clock_timestamp() AND clock_timestamp()<v.valid_until))) AS verified_upper_time
  FROM fi.commitment_batches b LEFT JOIN fi.batch_seals s ON s.batch_id=b.id;

CREATE OR REPLACE FUNCTION fi.score_experiment(e uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; b fi.batch_catalog; reg fi.batch_catalog; ctx text; hash text; run uuid; row record;
  task jsonb; sub jsonb; probs jsonb; outcome_row fi.outcome_catalog; first_claim timestamptz;
  ok_time boolean; ps numeric; base numeric; loss text; bins int; y int; i int; cdf numeric; target numeric; uniform_cdf numeric;
  primary_val numeric; state_text text; metric_text text; cohort text;
  registration_time timestamptz; forecast_time timestamptz; time_evidence jsonb;
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
  -- Historical qualification is based on recorded checks and actual event
  -- times, not on whether a once-valid CRL is still fresh at scoring time.
  SELECT min(v.upper_time) INTO registration_time FROM fi.anchor_time_catalog v
    WHERE v.batch_id=reg.id AND v.upper_time<=first_claim AND v.checked_at<=first_claim
      AND (v.valid_from IS NULL OR (v.valid_from<=first_claim AND first_claim<v.valid_until));
  SELECT min(v.upper_time) INTO forecast_time FROM fi.anchor_time_catalog v WHERE v.batch_id=b.id;
  SELECT coalesce(jsonb_agg(jsonb_build_object('request_id',v.request_id,'batch_id',v.batch_id,
      'checked_at',v.checked_at,'verification_sha256',v.verification_sha256) ORDER BY v.request_id),'[]'::jsonb)
    INTO time_evidence FROM fi.anchor_time_catalog v WHERE v.batch_id IN(reg.id,b.id);
  ok_time:=coalesce(x.spec->>'run_kind'='LIVE' AND NOT(reg.trust_profile->>'fixture')::bool
    AND registration_time<=first_claim AND forecast_time<x.deadline
    AND NOT EXISTS(SELECT 1 FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id
      CROSS JOIN LATERAL (VALUES(a.claimed_at),(a.call_started_at)) AS attempt_time(at_time)
      WHERE t.experiment_id=e AND attempt_time.at_time IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM fi.anchor_time_catalog v WHERE v.batch_id=reg.id
          AND v.upper_time<=attempt_time.at_time AND v.checked_at<=attempt_time.at_time
          AND (v.valid_from IS NULL OR (v.valid_from<=attempt_time.at_time AND attempt_time.at_time<v.valid_until)))),false);
  SELECT jsonb_build_object('algorithm','fi.score.v3','experiment_id',e,'run_kind',x.spec->>'run_kind',
    'registration_subject',reg.subject_sha256,'forecast_subject',b.subject_sha256,'eligible_time',ok_time,
    'registration_upper_time',registration_time,'forecast_upper_time',forecast_time,'first_claim',first_claim,
    'time_evidence',time_evidence,
    'outcomes',jsonb_agg(to_jsonb(o) ORDER BY o.version_id))::text INTO ctx FROM fi.outcome_catalog o
    WHERE o.version_id IN(SELECT question_version_id FROM fi.expected_tasks WHERE experiment_id=e);
  hash:=encode(sha256(convert_to(ctx,'UTF8')),'hex');
  SELECT id INTO run FROM fi.score_runs WHERE experiment_id=e AND algorithm='fi.score.v3' AND context_sha256=hash;
  IF FOUND THEN RETURN run; END IF;
  INSERT INTO fi.score_runs(experiment_id,algorithm,context_text,context_sha256) VALUES(e,'fi.score.v3',ctx,hash) RETURNING id INTO run;
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
