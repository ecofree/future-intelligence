-- P0-03. Forward migration; no rewriting historical ledger migrations.
-- Real HTTP requests are never made inside SQL transactions.
CREATE TABLE fi.evidence_seals (
  experiment_id uuid PRIMARY KEY REFERENCES fi.experiments(id),
  request_sha256 text NOT NULL,
  manifest_text text NOT NULL,
  manifest_sha256 text NOT NULL,
  observed_at timestamptz NOT NULL,
  created_by name NOT NULL DEFAULT session_user
);
CREATE TABLE fi.evidence_packets (
  experiment_id uuid NOT NULL REFERENCES fi.experiments(id),
  question_version_id uuid NOT NULL REFERENCES fi.question_versions(id),
  packet_text text NOT NULL,
  packet_sha256 text NOT NULL,
  PRIMARY KEY(experiment_id, question_version_id)
);
CREATE TABLE fi.provider_requests (
  attempt_id uuid PRIMARY KEY REFERENCES fi.task_attempts(id),
  task_id uuid NOT NULL REFERENCES fi.expected_tasks(id),
  provider text NOT NULL,
  model_requested text NOT NULL,
  evidence_sha256 text NOT NULL,
  request_text text NOT NULL,
  request_sha256 text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.provider_observations (
  attempt_id uuid PRIMARY KEY REFERENCES fi.provider_requests(attempt_id),
  observation jsonb NOT NULL,
  observation_sha256 text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['evidence_seals','evidence_packets','provider_requests','provider_observations'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.%I FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation()',t);
  END LOOP;
END $$;

CREATE FUNCTION fi.seal_evidence(e uuid,packets jsonb) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; old fi.evidence_seals; p jsonb; item jsonb; qid uuid; v uuid;
  sid uuid; pid uuid; refs uuid[]; request_hash text; manifest text; packet text; moment timestamptz;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF packets IS NULL OR jsonb_typeof(packets)<>'array' OR octet_length(packets::text)>10000000 THEN
    RAISE EXCEPTION 'invalid evidence batch' USING ERRCODE='22023'; END IF;
  request_hash:=encode(sha256(convert_to(packets::text,'UTF8')),'hex');
  SELECT * INTO old FROM fi.evidence_seals WHERE experiment_id=e;
  IF FOUND THEN
    IF old.request_sha256<>request_hash THEN RAISE EXCEPTION 'evidence already sealed with different content' USING ERRCODE='55000'; END IF;
    RETURN old.manifest_sha256;
  END IF;
  IF EXISTS(SELECT 1 FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=e) THEN
    RAISE EXCEPTION 'cannot seal after any attempt' USING ERRCODE='55000'; END IF;
  IF jsonb_array_length(packets)<>(SELECT count(DISTINCT question_version_id) FROM fi.expected_tasks WHERE experiment_id=e) THEN
    RAISE EXCEPTION 'evidence must cover entire registered question set' USING ERRCODE='22023'; END IF;
  refs:=ARRAY[]::uuid[];
  FOR p IN SELECT * FROM jsonb_array_elements(packets) LOOP
    IF jsonb_typeof(p)<>'object' OR NOT(p ?& ARRAY['question_version_id','items'])
      OR (SELECT count(*) FROM jsonb_object_keys(p))<>2 OR jsonb_typeof(p->'items')<>'array' THEN
      RAISE EXCEPTION 'invalid packet fields' USING ERRCODE='22023'; END IF;
    qid:=(p->>'question_version_id')::uuid;
    IF qid IS NULL OR qid=ANY(refs) OR NOT EXISTS(SELECT 1 FROM fi.expected_tasks WHERE experiment_id=e AND question_version_id=qid)
      OR jsonb_array_length(p->'items') NOT BETWEEN 1 AND 30 THEN
      RAISE EXCEPTION 'unknown, duplicate or empty packet' USING ERRCODE='22023'; END IF;
    refs:=array_append(refs,qid);
    IF (SELECT count(DISTINCT z->>'id') FROM jsonb_array_elements(p->'items') z)<>jsonb_array_length(p->'items') THEN
      RAISE EXCEPTION 'duplicate evidence id' USING ERRCODE='22023'; END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(p->'items') LOOP
      IF jsonb_typeof(item)<>'object' OR NOT(item ?& ARRAY['id','source_id','permission_id','title','text'])
        OR (SELECT count(*) FROM jsonb_object_keys(item))<>5
        OR jsonb_typeof(item->'id') IS DISTINCT FROM 'string' OR (item->>'id')!~'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
        OR jsonb_typeof(item->'title') IS DISTINCT FROM 'string' OR length(btrim(item->>'title')) NOT BETWEEN 1 AND 500
        OR jsonb_typeof(item->'text') IS DISTINCT FROM 'string' OR octet_length(item->>'text') NOT BETWEEN 1 AND 100000 THEN
        RAISE EXCEPTION 'invalid evidence item' USING ERRCODE='22023'; END IF;
      sid:=(item->>'source_id')::uuid; pid:=(item->>'permission_id')::uuid;
      IF NOT EXISTS(SELECT 1 FROM fi.question_sources WHERE version_id=qid AND source_id=sid AND permission_id=pid) THEN
        RAISE EXCEPTION 'evidence source not in frozen question' USING ERRCODE='42501'; END IF;
    END LOOP;
  END LOOP;
  -- Lock all sources in one global order before examining any permission.
  FOR sid IN SELECT DISTINCT s.source_id FROM fi.question_sources s WHERE s.version_id=ANY(refs) ORDER BY s.source_id LOOP
    PERFORM 1 FROM fi.sources WHERE id=sid FOR SHARE;
  END LOOP;
  FOREACH v IN ARRAY refs LOOP PERFORM fi.assert_sources(v); END LOOP;
  moment:=clock_timestamp();
  IF moment>=x.deadline THEN RAISE EXCEPTION 'evidence deadline passed' USING ERRCODE='55000'; END IF;
  FOR p IN SELECT * FROM jsonb_array_elements(packets) LOOP
    qid:=(p->>'question_version_id')::uuid;
    packet:=jsonb_build_object('protocol','fi.evidence-packet.v1','question_version_id',qid,
      'observed_at',to_char(moment AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
      'acquisition','OPERATOR_SUBMITTED_SNAPSHOT','items',p->'items')::text;
    INSERT INTO fi.evidence_packets VALUES(e,qid,packet,encode(sha256(convert_to(packet,'UTF8')),'hex'));
  END LOOP;
  SELECT jsonb_build_object('protocol','fi.evidence-manifest.v1','experiment_id',e,
    'experiment_manifest_sha256',x.manifest_sha256,
    'packets',jsonb_agg(jsonb_build_object('question_version_id',question_version_id,'sha256',packet_sha256)
      ORDER BY question_version_id))::text INTO manifest FROM fi.evidence_packets WHERE experiment_id=e;
  INSERT INTO fi.evidence_seals VALUES(e,request_hash,manifest,encode(sha256(convert_to(manifest,'UTF8')),'hex'),moment,session_user);
  PERFORM fi.ledger_emit(e,NULL,'EVIDENCE_SEALED',jsonb_build_object('manifest_sha256',encode(sha256(convert_to(manifest,'UTF8')),'hex')));
  RETURN encode(sha256(convert_to(manifest,'UTF8')),'hex');
END $$;

ALTER FUNCTION fi.claim_task(uuid,text) RENAME TO claim_task_p002;
REVOKE ALL ON FUNCTION fi.claim_task_p002(uuid,text) FROM PUBLIC,fi_worker;
CREATE FUNCTION fi.claim_task(e uuid,p_kind text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  PERFORM fi.ledger_rc();
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF p_kind='LIVE' AND NOT EXISTS(SELECT 1 FROM fi.evidence_seals WHERE experiment_id=e) THEN
    RAISE EXCEPTION 'LIVE requires sealed evidence' USING ERRCODE='55000'; END IF;
  RETURN fi.claim_task_p002(e,p_kind);
END $$;

CREATE FUNCTION fi.live_input(t uuid) RETURNS jsonb LANGUAGE sql
SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT jsonb_build_object('protocol','fi.model-input.v1','question_spec',v.spec,
    'evidence',p.packet_text::jsonb,'evidence_sha256',p.packet_sha256)
  FROM fi.expected_tasks q JOIN fi.question_versions v ON v.id=q.question_version_id
    JOIN fi.evidence_packets p ON p.experiment_id=q.experiment_id AND p.question_version_id=q.question_version_id
  WHERE q.id=t
$$;
CREATE FUNCTION fi.live_context(t uuid,a uuid,f bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE data jsonb; BEGIN
  PERFORM fi.ledger_active(t,a,f);
  SELECT fi.live_input(t) INTO data;
  IF data IS NULL THEN RAISE EXCEPTION 'no frozen evidence' USING ERRCODE='55000'; END IF;
  RETURN data;
END $$;

ALTER FUNCTION fi.start_task_call(uuid,uuid,bigint) RENAME TO start_task_call_p002;
REVOKE ALL ON FUNCTION fi.start_task_call_p002(uuid,uuid,bigint) FROM PUBLIC,fi_worker;
CREATE FUNCTION fi.start_task_call(t uuid,a uuid,f bigint) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  PERFORM fi.ledger_active(t,a,f);
  IF EXISTS(SELECT 1 FROM fi.expected_tasks q JOIN fi.experiments e ON e.id=q.experiment_id
    WHERE q.id=t AND e.spec->>'run_kind'='LIVE') THEN
    RAISE EXCEPTION 'LIVE requires registered provider request' USING ERRCODE='55000'; END IF;
  RETURN fi.start_task_call_p002(t,a,f);
END $$;

CREATE FUNCTION fi.prepare_live_call(t uuid,a uuid,f bigint,body_text text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE q fi.expected_tasks; cfg jsonb; b jsonb; prompt text; input_text text; provider_name text;
  max_output integer; expected_input jsonb; allowed text[];
BEGIN
  PERFORM fi.ledger_active(t,a,f);
  SELECT * INTO STRICT q FROM fi.expected_tasks WHERE id=t;
  IF NOT EXISTS(SELECT 1 FROM fi.experiments WHERE id=q.experiment_id AND spec->>'run_kind'='LIVE') THEN
    RAISE EXCEPTION 'live entry required' USING ERRCODE='42501'; END IF;
  SELECT config INTO STRICT cfg FROM fi.experiment_entries WHERE experiment_id=q.experiment_id AND entry_key=q.entry_key;
  provider_name:=cfg->>'provider'; expected_input:=fi.live_input(t);
  IF body_text IS NULL OR octet_length(body_text)>2000000 OR expected_input IS NULL THEN
    RAISE EXCEPTION 'missing or oversized request/evidence' USING ERRCODE='22023'; END IF;
  b:=body_text::jsonb;
  IF jsonb_typeof(b)<>'object' THEN RAISE EXCEPTION 'request must be object' USING ERRCODE='22023'; END IF;
  IF provider_name='openai' THEN
    allowed:=ARRAY['model','instructions','input','max_output_tokens','tools','store','stream','text'];
    prompt:=b->>'instructions'; input_text:=b->>'input'; max_output:=(b->>'max_output_tokens')::integer;
    IF b->>'model' IS DISTINCT FROM cfg->>'model_id' OR b->'tools' IS DISTINCT FROM '[]'::jsonb
      OR b->'store' IS DISTINCT FROM 'false'::jsonb OR b->'stream' IS DISTINCT FROM 'false'::jsonb THEN
      RAISE EXCEPTION 'unsafe OpenAI request' USING ERRCODE='22023'; END IF;
  ELSIF provider_name='anthropic' THEN
    allowed:=ARRAY['model','system','messages','max_tokens','stream','output_config'];
    prompt:=b->>'system'; input_text:=b#>>'{messages,0,content}'; max_output:=(b->>'max_tokens')::integer;
    IF b->>'model' IS DISTINCT FROM cfg->>'model_id' OR b->'stream' IS DISTINCT FROM 'false'::jsonb
      OR b->'messages' IS DISTINCT FROM jsonb_build_array(jsonb_build_object('role','user','content',input_text)) THEN
      RAISE EXCEPTION 'unsafe Anthropic request' USING ERRCODE='22023'; END IF;
  ELSIF provider_name='google' THEN
    allowed:=ARRAY['systemInstruction','contents','generationConfig'];
    prompt:=b#>>'{systemInstruction,parts,0,text}'; input_text:=b#>>'{contents,0,parts,0,text}';
    max_output:=(b#>>'{generationConfig,maxOutputTokens}')::integer;
    IF b->'systemInstruction' IS DISTINCT FROM jsonb_build_object('parts',jsonb_build_array(jsonb_build_object('text',prompt)))
      OR b->'contents' IS DISTINCT FROM jsonb_build_array(jsonb_build_object('role','user','parts',jsonb_build_array(jsonb_build_object('text',input_text))))
      OR b#>'{generationConfig,candidateCount}' IS DISTINCT FROM '1'::jsonb THEN
      RAISE EXCEPTION 'unsafe Google request' USING ERRCODE='22023'; END IF;
  ELSE RAISE EXCEPTION 'unsupported provider' USING ERRCODE='22023'; END IF;
  IF NOT(b ?& allowed) OR (SELECT count(*) FROM jsonb_object_keys(b))<>cardinality(allowed)
    OR prompt IS NULL OR encode(sha256(convert_to(prompt,'UTF8')),'hex') IS DISTINCT FROM cfg->>'prompt_sha256'
    OR input_text IS NULL OR input_text::jsonb IS DISTINCT FROM expected_input
    OR max_output IS NULL OR max_output IS DISTINCT FROM (cfg#>>'{parameters,max_output_tokens}')::integer
    OR max_output NOT BETWEEN 128 AND 16384 THEN
    RAISE EXCEPTION 'request does not match frozen input/config' USING ERRCODE='22023'; END IF;
  IF NOT fi.start_task_call_p002(t,a,f) THEN RETURN false; END IF;
  INSERT INTO fi.provider_requests(attempt_id,task_id,provider,model_requested,evidence_sha256,request_text,request_sha256)
    VALUES(a,t,provider_name,cfg->>'model_id',expected_input->>'evidence_sha256',body_text,
      encode(sha256(convert_to(body_text,'UTF8')),'hex'));
  RETURN true;
END $$;

CREATE FUNCTION fi.record_provider_observation(t uuid,a uuid,f bigint,obs jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE att fi.task_attempts; old fi.provider_observations; digest text; k text; q fi.expected_tasks;
  allowed text[]:=ARRAY['protocol','status','http_status','response_sha256','model_reported','request_id','usage','output'];
BEGIN
  PERFORM fi.ledger_lock(t);
  SELECT * INTO STRICT att FROM fi.task_attempts WHERE id=a AND task_id=t;
  IF att.owner<>session_user OR att.fence IS DISTINCT FROM f OR NOT EXISTS(SELECT 1 FROM fi.provider_requests WHERE attempt_id=a) THEN
    RAISE EXCEPTION 'foreign or unregistered call' USING ERRCODE='42501'; END IF;
  IF obs IS NULL OR jsonb_typeof(obs)<>'object' OR NOT(obs ?& allowed)
    OR (SELECT count(*) FROM jsonb_object_keys(obs))<>cardinality(allowed) OR octet_length(obs::text)>32000
    OR obs->>'protocol' IS DISTINCT FROM 'fi.provider-observation.v1'
    OR coalesce(obs->>'status','') NOT IN ('OK','INVALID_OUTPUT','INCOMPLETE','REFUSED','HTTP_ERROR','TIMEOUT','TRANSPORT_ERROR','RESPONSE_TOO_LARGE') THEN
    RAISE EXCEPTION 'invalid sanitized observation' USING ERRCODE='22023'; END IF;
  IF obs->>'response_sha256' IS NOT NULL AND (obs->>'response_sha256')!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid response digest' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['model_reported','request_id'] LOOP
    IF obs->>k IS NOT NULL AND (jsonb_typeof(obs->k)<>'string' OR (obs->>k)!~'^[A-Za-z0-9_./:-]{1,200}$') THEN
      RAISE EXCEPTION 'invalid public identifier' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF obs->'http_status'<>'null'::jsonb AND (jsonb_typeof(obs->'http_status')<>'number'
    OR (obs->>'http_status')!~'^[1-5][0-9][0-9]$') THEN
    RAISE EXCEPTION 'invalid http status' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(obs->'usage') IS DISTINCT FROM 'object'
    OR NOT((obs->'usage') ?& ARRAY['input_tokens','output_tokens','cached_input_tokens'])
    OR (SELECT count(*) FROM jsonb_object_keys(obs->'usage'))<>3 THEN
    RAISE EXCEPTION 'invalid usage fields' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['input_tokens','output_tokens','cached_input_tokens'] LOOP
    IF obs#>>ARRAY['usage',k] IS NOT NULL AND (jsonb_typeof(obs#>ARRAY['usage',k])<>'number'
      OR (obs#>>ARRAY['usage',k])!~'^[0-9]{1,10}$' OR (obs#>>ARRAY['usage',k])::bigint>1000000000) THEN
      RAISE EXCEPTION 'invalid usage count' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF obs->>'status'='OK' THEN
    IF obs->>'response_sha256' IS NULL OR obs->'http_status'<>'200'::jsonb
      OR jsonb_typeof(obs->'output') IS DISTINCT FROM 'object'
      OR NOT((obs->'output') ?& ARRAY['probabilities_ppm','rationale','evidence_ids'])
      OR (SELECT count(*) FROM jsonb_object_keys(obs->'output'))<>3
      OR jsonb_typeof(obs#>'{output,rationale}') IS DISTINCT FROM 'string'
      OR length(obs#>>'{output,rationale}') NOT BETWEEN 1 AND 4000
      OR jsonb_typeof(obs#>'{output,evidence_ids}') IS DISTINCT FROM 'array'
      OR jsonb_typeof(obs#>'{output,probabilities_ppm}') IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'invalid output observation' USING ERRCODE='22023'; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(obs#>'{output,evidence_ids}') i
      WHERE jsonb_typeof(i)<>'string' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(fi.live_input(t)#>'{evidence,items}') e WHERE e->>'id'=i#>>'{}')) THEN
      RAISE EXCEPTION 'unknown evidence citation' USING ERRCODE='22023'; END IF;
  ELSIF obs->'output' IS DISTINCT FROM 'null'::jsonb THEN
    RAISE EXCEPTION 'failure must not contain output' USING ERRCODE='22023'; END IF;
  digest:=encode(sha256(convert_to(obs::text,'UTF8')),'hex');
  SELECT * INTO old FROM fi.provider_observations WHERE attempt_id=a;
  IF FOUND THEN
    IF old.observation_sha256<>digest THEN RAISE EXCEPTION 'conflicting observation' USING ERRCODE='55000'; END IF;
    RETURN digest;
  END IF;
  -- Late usage evidence survives. It DOES NOT revive an expired task lease.
  INSERT INTO fi.provider_observations VALUES(a,obs,digest,clock_timestamp());
  SELECT * INTO q FROM fi.expected_tasks WHERE id=t;
  PERFORM fi.ledger_emit(q.experiment_id,t,'PROVIDER_OBSERVED',jsonb_build_object('attempt_id',a,'status',obs->>'status'));
  RETURN digest;
END $$;

ALTER FUNCTION fi.finish_task(uuid,uuid,bigint,jsonb,text,bigint,text,boolean) RENAME TO finish_task_p002;
REVOKE ALL ON FUNCTION fi.finish_task_p002(uuid,uuid,bigint,jsonb,text,bigint,text,boolean) FROM PUBLIC,fi_worker;
CREATE FUNCTION fi.finish_task(t uuid,a uuid,f bigint,probs jsonb,response_hash text,actual bigint,error_code text,retryable boolean)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE obs jsonb; started timestamptz;
BEGIN
  PERFORM fi.ledger_lock(t);
  IF EXISTS(SELECT 1 FROM fi.expected_tasks q JOIN fi.experiments e ON e.id=q.experiment_id WHERE q.id=t AND e.spec->>'run_kind'='LIVE') THEN
    SELECT call_started_at INTO STRICT started FROM fi.task_attempts WHERE id=a AND task_id=t;
    IF started IS NOT NULL THEN
      SELECT observation INTO obs FROM fi.provider_observations WHERE attempt_id=a;
      IF obs IS NULL OR actual IS NOT NULL OR retryable IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'LIVE requires observation; cost stays unknown pending reconciliation' USING ERRCODE='55000'; END IF;
      IF probs IS NOT NULL THEN
        IF obs->>'status'<>'OK' OR probs IS DISTINCT FROM obs#>'{output,probabilities_ppm}'
          OR response_hash IS DISTINCT FROM obs->>'response_sha256' THEN
          RAISE EXCEPTION 'result does not match observation' USING ERRCODE='22023'; END IF;
      ELSIF error_code IS DISTINCT FROM obs->>'status' OR obs->>'status'='OK' THEN
        RAISE EXCEPTION 'error does not match observation' USING ERRCODE='22023'; END IF;
    ELSIF probs IS NOT NULL THEN RAISE EXCEPTION 'LIVE needs registered dispatch' USING ERRCODE='55000'; END IF;
  END IF;
  RETURN fi.finish_task_p002(t,a,f,probs,response_hash,actual,error_code,retryable);
END $$;

REVOKE ALL ON fi.evidence_seals,fi.evidence_packets,fi.provider_requests,fi.provider_observations FROM PUBLIC;
REVOKE ALL ON FUNCTION fi.seal_evidence(uuid,jsonb),fi.claim_task(uuid,text),fi.live_input(uuid),
  fi.live_context(uuid,uuid,bigint),fi.start_task_call(uuid,uuid,bigint),fi.prepare_live_call(uuid,uuid,bigint,text),
  fi.record_provider_observation(uuid,uuid,bigint,jsonb),fi.finish_task(uuid,uuid,bigint,jsonb,text,bigint,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.seal_evidence(uuid,jsonb) TO fi_operator;
GRANT SELECT ON fi.evidence_seals,fi.evidence_packets,fi.provider_requests,fi.provider_observations TO fi_operator;
GRANT EXECUTE ON FUNCTION fi.claim_task(uuid,text),fi.live_context(uuid,uuid,bigint),
  fi.start_task_call(uuid,uuid,bigint),fi.prepare_live_call(uuid,uuid,bigint,text),
  fi.record_provider_observation(uuid,uuid,bigint,jsonb),fi.finish_task(uuid,uuid,bigint,jsonb,text,bigint,text,boolean) TO fi_worker;
