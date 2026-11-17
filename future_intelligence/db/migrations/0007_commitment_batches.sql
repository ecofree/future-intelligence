-- P0-04. Exact record bytes + complete task set; SQL does not verify PKI signatures.
-- The isolated verifier validates exported proof bytes. Public auditors must reverify.
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['fi_verifier','fi_scorekeeper','fi_resolution_reviewer','fi_dashboard'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',r);
    ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolcanlogin OR rolbypassrls)) THEN
      RAISE EXCEPTION 'unsafe role: %',r;
    END IF;
  END LOOP;
END $$;
CREATE TABLE fi.commitment_batches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), experiment_id uuid NOT NULL REFERENCES fi.experiments,
  kind text NOT NULL CHECK(kind IN ('REGISTRATION','FORECAST')),
  analysis_plan jsonb NOT NULL, trust_profile jsonb NOT NULL,
  captured_at timestamptz NOT NULL DEFAULT clock_timestamp(), created_by name NOT NULL DEFAULT session_user,
  UNIQUE(experiment_id,kind)
);
CREATE TABLE fi.batch_records (
  batch_id uuid REFERENCES fi.commitment_batches, ordinal integer NOT NULL CHECK(ordinal>=0),
  record_text text NOT NULL, PRIMARY KEY(batch_id,ordinal)
);
CREATE TABLE fi.batch_seals (
  batch_id uuid PRIMARY KEY REFERENCES fi.commitment_batches, nonces text[] NOT NULL,
  root_sha256 text NOT NULL CHECK(root_sha256 ~ '^[0-9a-f]{64}$'),
  subject_sha256 text NOT NULL CHECK(subject_sha256 ~ '^[0-9a-f]{64}$'),
  sealed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.anchor_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), batch_id uuid NOT NULL REFERENCES fi.batch_seals,
  protocol text NOT NULL CHECK(protocol IN ('RFC3161','OTS')),
  request_bytes bytea NOT NULL CHECK(octet_length(request_bytes) BETWEEN 1 AND 2000000),
  request_sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by name NOT NULL DEFAULT session_user, UNIQUE(batch_id,protocol,request_sha256)
);
CREATE TABLE fi.anchor_replies (
  request_id uuid PRIMARY KEY REFERENCES fi.anchor_requests,
  status text NOT NULL CHECK(status IN ('RECEIVED','FAILED','PENDING_UNVERIFIED')),
  reply_bytes bytea CHECK(octet_length(reply_bytes) BETWEEN 1 AND 2000000),
  error_code text CHECK(error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK((status='FAILED' AND reply_bytes IS NULL AND error_code IS NOT NULL)
    OR(status<>'FAILED' AND reply_bytes IS NOT NULL AND error_code IS NULL))
);
CREATE TABLE fi.anchor_verifications (
  request_id uuid PRIMARY KEY REFERENCES fi.anchor_replies,
  status text NOT NULL CHECK(status IN ('VERIFIED','VERIFIED_LATE')),
  upper_time timestamptz NOT NULL, report jsonb NOT NULL,
  verifier_login name NOT NULL DEFAULT session_user, verified_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION fi.validate_delivery_plan(plan jsonb,profile jsonb) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE k text; n integer; BEGIN
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
  IF profile IS NULL OR jsonb_typeof(profile)<>'object'
    OR NOT(profile ?& ARRAY['ca_sha256','policy_oids','max_accuracy_us','missing_accuracy_us','fixture'])
    OR (SELECT count(*) FROM jsonb_object_keys(profile))<>5
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

CREATE FUNCTION fi.capture_batch(e uuid,k text,plan jsonb DEFAULT NULL,profile jsonb DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; old fi.commitment_batches; reg fi.commitment_batches;
  bid uuid:=gen_random_uuid(); t record; txt text; n integer:=0; questions jsonb; c jsonb;
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
  FOR t IN SELECT q.*,cfg.config_sha256,st.status,sub.snapshot_text,pr.request_sha256,po.observation_sha256
      FROM fi.expected_tasks q JOIN fi.experiment_entries cfg ON cfg.experiment_id=e AND cfg.entry_key=q.entry_key
      JOIN fi.task_states st ON st.task_id=q.id LEFT JOIN fi.task_submissions sub ON sub.task_id=q.id
      LEFT JOIN fi.provider_requests pr ON pr.attempt_id=sub.attempt_id LEFT JOIN fi.provider_observations po ON po.attempt_id=sub.attempt_id
      WHERE q.experiment_id=e ORDER BY q.id LOOP
    n:=n+1;
    c:=jsonb_build_object('record_type','TASK','task_id',t.id,'question_version_id',t.question_version_id,
      'entry_key',t.entry_key,'entry_config_sha256',t.config_sha256);
    IF k='FORECAST' THEN c:=c||jsonb_build_object('status_at_seal',t.status,'submission',t.snapshot_text::jsonb,
      'provider_request_sha256',t.request_sha256,'provider_observation_sha256',t.observation_sha256); END IF;
    INSERT INTO fi.batch_records VALUES(bid,n,c::text);
  END LOOP;
  PERFORM fi.ledger_emit(e,NULL,'BATCH_CAPTURED',jsonb_build_object('batch_id',bid,'kind',k,'task_count',n));
  RETURN bid;
END $$;

CREATE FUNCTION fi.batch_digest(b uuid,nonces text[]) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE row record; leaves bytea[]:=ARRAY[]::bytea[]; next_layer bytea[]; h bytea; i int; count_rows int; k text;
BEGIN
  SELECT kind INTO STRICT k FROM fi.commitment_batches WHERE id=b;
  SELECT count(*) INTO count_rows FROM fi.batch_records WHERE batch_id=b;
  IF nonces IS NULL OR cardinality(nonces)<>count_rows OR array_position(nonces,NULL) IS NOT NULL
    OR (SELECT count(DISTINCT x) FROM unnest(nonces) x)<>count_rows THEN
    RAISE EXCEPTION 'one distinct nonce per record required' USING ERRCODE='22023'; END IF;
  FOR row IN SELECT * FROM fi.batch_records WHERE batch_id=b ORDER BY ordinal LOOP
    IF nonces[row.ordinal+1]!~'^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid nonce' USING ERRCODE='22023'; END IF;
    h:=sha256(convert_to('FI:batch-record:v1','UTF8')||decode('00','hex')||decode(nonces[row.ordinal+1],'hex')||convert_to(row.record_text,'UTF8'));
    leaves:=array_append(leaves,sha256(decode('00','hex')||h));
  END LOOP;
  WHILE cardinality(leaves)>1 LOOP
    next_layer:=ARRAY[]::bytea[];i:=1;
    WHILE i<=cardinality(leaves) LOOP
      next_layer:=array_append(next_layer,sha256(decode('01','hex')||leaves[i]||leaves[least(i+1,cardinality(leaves))]));i:=i+2;
    END LOOP;leaves:=next_layer;
  END LOOP;
  h:=sha256(convert_to('FI:batch-root:v1','UTF8')||decode('00','hex')||convert_to(b::text,'UTF8')||decode('00','hex')||convert_to(k,'UTF8')||
    decode('00','hex')||leaves[1]||decode('00','hex')||convert_to(count_rows::text,'UTF8'));
  RETURN jsonb_build_object('root_sha256',encode(leaves[1],'hex'),'subject_sha256',encode(h,'hex'));
END $$;
CREATE FUNCTION fi.seal_batch(b uuid,nonces text[],root_hash text,subject_hash text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE old fi.batch_seals; d jsonb; BEGIN
  PERFORM 1 FROM fi.commitment_batches WHERE id=b FOR UPDATE;
  SELECT * INTO old FROM fi.batch_seals WHERE batch_id=b;
  IF FOUND THEN
    IF old.nonces IS DISTINCT FROM nonces OR old.root_sha256 IS DISTINCT FROM root_hash OR old.subject_sha256 IS DISTINCT FROM subject_hash THEN
      RAISE EXCEPTION 'seal conflict' USING ERRCODE='22023'; END IF;RETURN old.subject_sha256;
  END IF;
  d:=fi.batch_digest(b,nonces);
  IF root_hash IS DISTINCT FROM d->>'root_sha256' OR subject_hash IS DISTINCT FROM d->>'subject_sha256' THEN
    RAISE EXCEPTION 'seal does not match frozen records' USING ERRCODE='22023'; END IF;
  INSERT INTO fi.batch_seals(batch_id,nonces,root_sha256,subject_sha256) VALUES(b,nonces,root_hash,subject_hash);
  RETURN subject_hash;
END $$;

CREATE FUNCTION fi.reject_after_forecast_capture() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE e uuid; BEGIN
  SELECT experiment_id INTO e FROM fi.expected_tasks WHERE id=NEW.task_id;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF EXISTS(SELECT 1 FROM fi.commitment_batches WHERE experiment_id=e AND kind='FORECAST') THEN
    RAISE EXCEPTION 'forecast batch closed; late work cannot change committed predictions' USING ERRCODE='55000'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER closed_prediction_batch BEFORE INSERT ON fi.task_submissions FOR EACH ROW EXECUTE FUNCTION fi.reject_after_forecast_capture();
CREATE TRIGGER closed_attempt_batch BEFORE INSERT ON fi.task_attempts FOR EACH ROW EXECUTE FUNCTION fi.reject_after_forecast_capture();
CREATE TRIGGER closed_request_batch BEFORE INSERT ON fi.provider_requests FOR EACH ROW EXECUTE FUNCTION fi.reject_after_forecast_capture();

CREATE FUNCTION fi.request_anchor(b uuid,kind text,body bytea) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE rid uuid; h text; BEGIN
  h:=encode(sha256(body),'hex');
  INSERT INTO fi.anchor_requests(batch_id,protocol,request_bytes,request_sha256) VALUES(b,kind,body,h)
    ON CONFLICT(batch_id,protocol,request_sha256) DO NOTHING RETURNING id INTO rid;
  IF rid IS NULL THEN SELECT id INTO STRICT rid FROM fi.anchor_requests WHERE batch_id=b AND protocol=kind AND request_sha256=h; END IF;
  RETURN rid;
END $$;
CREATE FUNCTION fi.receive_anchor(r uuid,body bytea,code text DEFAULT NULL) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE k text; old fi.anchor_replies; BEGIN
  SELECT protocol INTO STRICT k FROM fi.anchor_requests WHERE id=r FOR UPDATE;
  SELECT * INTO old FROM fi.anchor_replies WHERE request_id=r;
  IF FOUND THEN
    IF old.reply_bytes IS DISTINCT FROM body OR old.error_code IS DISTINCT FROM code THEN RAISE EXCEPTION 'reply conflict' USING ERRCODE='22023'; END IF;RETURN;
  END IF;
  INSERT INTO fi.anchor_replies(request_id,status,reply_bytes,error_code)
    VALUES(r,CASE WHEN code IS NOT NULL THEN 'FAILED' WHEN k='OTS' THEN 'PENDING_UNVERIFIED' ELSE 'RECEIVED' END,body,code);
END $$;
CREATE FUNCTION fi.attest_anchor(r uuid,result jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
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
    OR result->>'status' IS DISTINCT FROM (CASE WHEN upper_at<=deadline THEN 'VERIFIED' ELSE 'VERIFIED_LATE' END)
    OR (result->>'accuracy_us')::bigint>(b.trust_profile->>'max_accuracy_us')::bigint THEN
    RAISE EXCEPTION 'invalid timestamp time bounds' USING ERRCODE='22023'; END IF;
  SELECT * INTO old FROM fi.anchor_verifications WHERE request_id=r;
  IF FOUND THEN
    IF old.report IS DISTINCT FROM result THEN RAISE EXCEPTION 'verification conflict' USING ERRCODE='22023'; END IF; RETURN;
  END IF;
  INSERT INTO fi.anchor_verifications(request_id,status,upper_time,report) VALUES(r,result->>'status',upper_at,result);
  PERFORM fi.audit(b.id,'EXTERNAL_TIME_VERIFIED',jsonb_build_object('request_id',r,'status',result->>'status','fixture',result->'fixture'));
END $$;
CREATE VIEW fi.batch_catalog AS SELECT b.*,s.root_sha256,s.subject_sha256,s.sealed_at,
  (SELECT count(*) FROM fi.batch_records r WHERE r.batch_id=b.id) AS record_count,
  (SELECT min(v.upper_time) FROM fi.anchor_requests a JOIN fi.anchor_verifications v ON v.request_id=a.id
    WHERE a.batch_id=b.id AND v.status='VERIFIED') AS verified_upper_time
  FROM fi.commitment_batches b LEFT JOIN fi.batch_seals s ON s.batch_id=b.id;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['commitment_batches','batch_records','batch_seals','anchor_requests','anchor_replies','anchor_verifications'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.%I FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('REVOKE ALL ON fi.%I FROM PUBLIC',t);
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION fi.validate_delivery_plan(jsonb,jsonb),fi.capture_batch(uuid,text,jsonb,jsonb),fi.batch_digest(uuid,text[]),
  fi.seal_batch(uuid,text[],text,text),fi.reject_after_forecast_capture(),fi.request_anchor(uuid,text,bytea),
  fi.receive_anchor(uuid,bytea,text),fi.attest_anchor(uuid,jsonb) FROM PUBLIC;
GRANT USAGE ON SCHEMA fi TO fi_verifier,fi_scorekeeper,fi_resolution_reviewer,fi_dashboard;
GRANT SELECT ON fi.commitment_batches,fi.batch_records,fi.batch_seals,fi.anchor_requests,fi.anchor_replies,fi.anchor_verifications,fi.batch_catalog TO fi_operator,fi_verifier,fi_scorekeeper;
GRANT EXECUTE ON FUNCTION fi.capture_batch(uuid,text,jsonb,jsonb),fi.seal_batch(uuid,text[],text,text),
  fi.request_anchor(uuid,text,bytea),fi.receive_anchor(uuid,bytea,text) TO fi_operator;
GRANT EXECUTE ON FUNCTION fi.attest_anchor(uuid,jsonb) TO fi_verifier;
-- No resolver/worker grant on proof records: forecast batch records contain predictions.

GRANT SELECT ON fi.experiments TO fi_verifier;
