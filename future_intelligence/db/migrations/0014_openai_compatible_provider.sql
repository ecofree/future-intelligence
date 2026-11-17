-- Forward only. Adds the config-driven `openai_compatible` LIVE provider on a
-- frozen endpoint profile (DeepSeek, DashScope, OpenRouter, relays). The profile
-- protocol is `openai_chat` (Chat Completions) or `anthropic_messages`
-- (Anthropic Messages shape on a relay). Existing provider semantics are unchanged; the three
-- functions below are verbatim copies of 0003/0005/0006 plus one branch each.
-- See ADR-020.

CREATE FUNCTION fi.valid_compatible_endpoint(p jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT coalesce(jsonb_typeof(p)='object'
    AND p ?& ARRAY['name','base_url','protocol','api_key_env','response_format']
    AND (SELECT count(*) FROM jsonb_object_keys(p))=5
    AND jsonb_typeof(p->'name')='string' AND jsonb_typeof(p->'base_url')='string'
    AND jsonb_typeof(p->'protocol')='string'
    AND (p->>'protocol') IN ('openai_chat','anthropic_messages')
    AND ((p->>'protocol')='openai_chat' OR (p->>'response_format')='json_schema')
    AND jsonb_typeof(p->'api_key_env')='string' AND jsonb_typeof(p->'response_format')='string'
    AND (p->>'name') ~ '^[a-z0-9][a-z0-9_-]{0,39}$'
    AND length(p->>'base_url')<=300 AND strpos(p->>'base_url','..')=0
    AND (p->>'base_url') ~ '^https://[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?(/[A-Za-z0-9._~-]+)*$'
    AND split_part(substr(p->>'base_url',9),'/',1) LIKE '%.%'
    AND (p->>'api_key_env') ~ '^[A-Z][A-Z0-9_]{2,63}$' AND (p->>'api_key_env') NOT LIKE 'FI\_%'
    AND (p->>'response_format') IN ('json_schema','json_object'), false)
$$;
REVOKE ALL ON FUNCTION fi.valid_compatible_endpoint(jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION fi.register_experiment(p_key text,p_versions uuid[],p_entries jsonb,p_spec jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE eid uuid:=gen_random_uuid(); old fi.experiments; v uuid; item jsonb; k text;
  versions uuid[]; entries jsonb; req text; manifest text; refs jsonb; tid uuid;
  keys text[]:=ARRAY['protocol','suite','run_kind','forecast_deadline','budget_microusd',
    'attempt_cap_microusd','max_attempts','lease_seconds','failure_policy','baseline_policy','disclosure_policy'];
  n bigint; moment timestamptz; cutoff timestamptz;
BEGIN
  PERFORM fi.ledger_rc();
  IF p_spec IS NULL OR jsonb_typeof(p_spec)<>'object' OR NOT(p_spec ?& keys)
    OR (SELECT count(*) FROM jsonb_object_keys(p_spec))<>cardinality(keys) THEN
    RAISE EXCEPTION 'invalid experiment spec keys' USING ERRCODE='22023'; END IF;
  IF p_spec->>'protocol' IS DISTINCT FROM 'fi.experiment.v1'
    OR p_spec->>'suite' IS DISTINCT FROM 'A'
    OR coalesce(p_spec->>'run_kind','') NOT IN ('FIXTURE','LIVE')
    OR p_spec->>'failure_policy' IS DISTINCT FROM 'REPORT_MISSING'
    OR p_spec->>'baseline_policy' IS DISTINCT FROM 'UNIFORM_CATEGORIES_V1'
    OR p_spec->>'disclosure_policy' IS DISTINCT FROM 'COMPLETE_BATCH_AFTER_RESOLUTION' THEN
    RAISE EXCEPTION 'unsupported experiment protocol or policy' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['budget_microusd','attempt_cap_microusd','max_attempts','lease_seconds'] LOOP
    IF jsonb_typeof(p_spec->k) IS DISTINCT FROM 'number' OR (p_spec->>k)!~'^[0-9]{1,13}$' THEN
      RAISE EXCEPTION 'positive integer required: %',k USING ERRCODE='22023'; END IF;
    n:=(p_spec->>k)::bigint;
    IF n<1 OR n>1000000000000 OR (k='max_attempts' AND n>5) OR (k='lease_seconds' AND n>3600) THEN
      RAISE EXCEPTION 'integer outside bounds: %',k USING ERRCODE='22023'; END IF;
  END LOOP;
  IF (p_spec->>'attempt_cap_microusd')::bigint>(p_spec->>'budget_microusd')::bigint
    OR jsonb_typeof(p_spec->'forecast_deadline') IS DISTINCT FROM 'string'
    OR (p_spec->>'forecast_deadline')!~'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' THEN
    RAISE EXCEPTION 'invalid cap or deadline' USING ERRCODE='22023'; END IF;
  cutoff:=(p_spec->>'forecast_deadline')::timestamptz;
  IF p_versions IS NULL OR cardinality(p_versions) NOT BETWEEN 1 AND 500 OR array_position(p_versions,NULL) IS NOT NULL
    OR cardinality(p_versions)<>(SELECT count(DISTINCT x) FROM unnest(p_versions) x) THEN
    RAISE EXCEPTION 'need 1-500 distinct question versions' USING ERRCODE='22023'; END IF;
  SELECT array_agg(x ORDER BY x) INTO versions FROM unnest(p_versions) x;
  IF p_entries IS NULL OR jsonb_typeof(p_entries)<>'array' THEN
    RAISE EXCEPTION 'entries must be array' USING ERRCODE='22023'; END IF;
  IF jsonb_array_length(p_entries) NOT BETWEEN 1 AND 10 THEN
    RAISE EXCEPTION 'need 1-10 entries' USING ERRCODE='22023'; END IF;
  FOR item IN SELECT * FROM jsonb_array_elements(p_entries) LOOP
    IF jsonb_typeof(item)<>'object' OR NOT(item ?& ARRAY['entry_key','provider','model_id','prompt_sha256','parameters'])
      OR (SELECT count(*) FROM jsonb_object_keys(item))<>5 THEN
      RAISE EXCEPTION 'invalid entry keys' USING ERRCODE='22023'; END IF;
    FOREACH k IN ARRAY ARRAY['entry_key','provider','model_id','prompt_sha256'] LOOP
      IF jsonb_typeof(item->k) IS DISTINCT FROM 'string' OR length(btrim(item->>k)) NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'invalid entry field' USING ERRCODE='22023'; END IF;
    END LOOP;
    IF (item->>'entry_key')!~'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
      OR (item->>'prompt_sha256')!~'^[0-9a-f]{64}$'
      OR jsonb_typeof(item->'parameters') IS DISTINCT FROM 'object'
      OR (p_spec->>'run_kind'='FIXTURE' AND item->>'provider'<>'fixture')
      OR (p_spec->>'run_kind'='LIVE' AND item->>'provider' NOT IN ('openai','anthropic','google','openai_compatible')) THEN
      RAISE EXCEPTION 'invalid entry identity or kind' USING ERRCODE='22023'; END IF;
    -- 0014: a compatible entry freezes its complete non-secret endpoint profile.
    IF item->>'provider'='openai_compatible' AND NOT fi.valid_compatible_endpoint(item#>'{parameters,endpoint}') THEN
      RAISE EXCEPTION 'invalid compatible endpoint profile' USING ERRCODE='22023'; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_object_keys(item->'parameters') z WHERE lower(z) IN
      ('api_key','apikey','authorization','password','secret','token')) THEN
      RAISE EXCEPTION 'secrets must not be registered' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF (SELECT count(DISTINCT x->>'entry_key') FROM jsonb_array_elements(p_entries) x)<>jsonb_array_length(p_entries) THEN
    RAISE EXCEPTION 'duplicate entry key' USING ERRCODE='22023'; END IF;
  SELECT jsonb_agg(x ORDER BY x->>'entry_key') INTO entries FROM jsonb_array_elements(p_entries) x;
  req:=encode(sha256(convert_to(jsonb_build_object('key',p_key,'versions',versions,'entries',entries,'spec',p_spec)::text,'UTF8')),'hex');
  PERFORM pg_advisory_xact_lock(hashtextextended('fi.experiment:'||p_key,0));
  SELECT * INTO old FROM fi.experiments WHERE external_key=p_key;
  IF FOUND THEN
    IF old.request_sha256<>req THEN RAISE EXCEPTION 'idempotency key conflict' USING ERRCODE='22023'; END IF;
    RETURN old.id;
  END IF;
  IF (SELECT count(DISTINCT question_id) FROM fi.question_versions WHERE id=ANY(versions))<>cardinality(versions) THEN
    RAISE EXCEPTION 'missing version or two versions of same question' USING ERRCODE='22023'; END IF;
  -- Global stable source order before per-version permission checks.
  PERFORM 1 FROM fi.question_states WHERE version_id=ANY(versions) ORDER BY version_id FOR SHARE;
  PERFORM 1 FROM fi.sources WHERE id IN
    (SELECT source_id FROM fi.question_sources WHERE version_id=ANY(versions)) ORDER BY id FOR SHARE;
  FOREACH v IN ARRAY versions LOOP
    PERFORM fi.assert_sources(v);
    IF NOT EXISTS(SELECT 1 FROM fi.question_states WHERE version_id=v AND status='FROZEN') THEN
      RAISE EXCEPTION 'experiment requires frozen question versions' USING ERRCODE='55000'; END IF;
  END LOOP;
  moment:=clock_timestamp();
  IF moment>=cutoff OR EXISTS(SELECT 1 FROM fi.question_versions WHERE id=ANY(versions) AND forecast_deadline<cutoff) THEN
    RAISE EXCEPTION 'experiment deadline passed or exceeds question cutoff' USING ERRCODE='55000'; END IF;
  SELECT jsonb_agg(jsonb_build_object('version_id',id,'snapshot_sha256',snapshot_sha256) ORDER BY id)
    INTO refs FROM fi.question_versions WHERE id=ANY(versions);
  manifest:=jsonb_build_object('protocol','fi.experiment-manifest.v1','external_key',p_key,
    'questions',refs,'entries',entries,'spec',p_spec)::text;
  INSERT INTO fi.experiments(id,external_key,spec,request_sha256,manifest_text,manifest_sha256,deadline)
    VALUES(eid,p_key,p_spec,req,manifest,encode(sha256(convert_to(manifest,'UTF8')),'hex'),cutoff);
  INSERT INTO fi.budget_accounts(experiment_id) VALUES(eid);
  FOR item IN SELECT * FROM jsonb_array_elements(entries) LOOP
    INSERT INTO fi.experiment_entries VALUES(eid,item->>'entry_key',item,encode(sha256(convert_to(item::text,'UTF8')),'hex'));
    FOREACH v IN ARRAY versions LOOP
      INSERT INTO fi.expected_tasks(experiment_id,question_version_id,entry_key)
        VALUES(eid,v,item->>'entry_key') RETURNING id INTO tid;
      INSERT INTO fi.task_states(task_id) VALUES(tid);
      PERFORM fi.ledger_emit(eid,tid,'TASK_READY',jsonb_build_object('attempt_no',0));
    END LOOP;
  END LOOP;
  PERFORM fi.ledger_emit(eid,NULL,'EXPERIMENT_REGISTERED',jsonb_build_object('tasks',cardinality(versions)*jsonb_array_length(entries)));
  RETURN eid;
END $$;

CREATE OR REPLACE FUNCTION fi.prepare_live_call(t uuid,a uuid,f bigint,body_text text) RETURNS boolean
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
  ELSIF provider_name='openai_compatible' AND cfg#>>'{parameters,endpoint,protocol}'='anthropic_messages' THEN
    allowed:=ARRAY['model','system','messages','max_tokens','stream','output_config'];
    prompt:=b->>'system'; input_text:=b#>>'{messages,0,content}'; max_output:=(b->>'max_tokens')::integer;
    IF b->>'model' IS DISTINCT FROM cfg->>'model_id' OR b->'stream' IS DISTINCT FROM 'false'::jsonb
      OR b->'messages' IS DISTINCT FROM jsonb_build_array(jsonb_build_object('role','user','content',input_text)) THEN
      RAISE EXCEPTION 'unsafe Anthropic-compatible request' USING ERRCODE='22023'; END IF;
  ELSIF provider_name='openai_compatible' AND cfg#>>'{parameters,endpoint,protocol}'='openai_chat' THEN
    allowed:=ARRAY['model','messages','max_tokens','stream','response_format'];
    prompt:=b#>>'{messages,0,content}'; input_text:=b#>>'{messages,1,content}'; max_output:=(b->>'max_tokens')::integer;
    IF b->>'model' IS DISTINCT FROM cfg->>'model_id' OR b->'stream' IS DISTINCT FROM 'false'::jsonb
      OR b->'messages' IS DISTINCT FROM jsonb_build_array(jsonb_build_object('role','system','content',prompt),
                                                          jsonb_build_object('role','user','content',input_text)) THEN
      RAISE EXCEPTION 'unsafe OpenAI-compatible request' USING ERRCODE='22023'; END IF;
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

CREATE OR REPLACE FUNCTION fi.provider_request_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE b jsonb; data text; wanted jsonb; fmt text; proto text;
  schema_def jsonb := '{"type":"object","additionalProperties":false,"properties":{"probabilities_ppm":{"type":"array","items":{"type":"integer"}},"rationale":{"type":"string"},"evidence_ids":{"type":"array","items":{"type":"string"}}},"required":["probabilities_ppm","rationale","evidence_ids"]}';
BEGIN
  IF NOT (NEW.request_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'request JSON keys must be unique' USING ERRCODE='22023'; END IF;
  b:=NEW.request_text::jsonb;
  IF NEW.provider='openai' THEN
    data:=b->>'input';
    wanted:=jsonb_build_object('format',jsonb_build_object('type','json_schema','name','forecast','strict',true,'schema',schema_def));
    IF b->'text' IS DISTINCT FROM wanted THEN
      RAISE EXCEPTION 'unregistered output contract' USING ERRCODE='22023'; END IF;
  ELSIF NEW.provider='anthropic' THEN
    data:=b#>>'{messages,0,content}';
    wanted:=jsonb_build_object('format',jsonb_build_object('type','json_schema','schema',schema_def));
    IF b->'output_config' IS DISTINCT FROM wanted THEN
      RAISE EXCEPTION 'unregistered output contract' USING ERRCODE='22023'; END IF;
  ELSIF NEW.provider='google' THEN
    data:=b#>>'{contents,0,parts,0,text}';
    wanted:=jsonb_build_object('maxOutputTokens',b#>'{generationConfig,maxOutputTokens}',
      'candidateCount',1,'responseMimeType','application/json','responseJsonSchema',schema_def);
    IF b->'generationConfig' IS DISTINCT FROM wanted THEN
      RAISE EXCEPTION 'unregistered output contract' USING ERRCODE='22023'; END IF;
  ELSIF NEW.provider='openai_compatible' THEN
    SELECT ee.config#>>'{parameters,endpoint,response_format}',ee.config#>>'{parameters,endpoint,protocol}' INTO fmt,proto
      FROM fi.expected_tasks q JOIN fi.experiment_entries ee ON ee.experiment_id=q.experiment_id AND ee.entry_key=q.entry_key
      WHERE q.id=NEW.task_id;
    IF proto='anthropic_messages' THEN
      data:=b#>>'{messages,0,content}';
      IF b->'output_config' IS DISTINCT FROM jsonb_build_object('format',jsonb_build_object('type','json_schema','schema',schema_def)) THEN
        RAISE EXCEPTION 'unregistered output contract' USING ERRCODE='22023'; END IF;
    ELSIF proto IS DISTINCT FROM 'openai_chat' THEN
      RAISE EXCEPTION 'unregistered compatible protocol' USING ERRCODE='22023';
    ELSE
    data:=b#>>'{messages,1,content}';
    wanted:=CASE fmt
      WHEN 'json_schema' THEN jsonb_build_object('type','json_schema','json_schema',
        jsonb_build_object('name','forecast','strict',true,'schema',schema_def))
      WHEN 'json_object' THEN jsonb_build_object('type','json_object') END;
    IF wanted IS NULL OR b->'response_format' IS DISTINCT FROM wanted THEN
      RAISE EXCEPTION 'unregistered output contract' USING ERRCODE='22023'; END IF;
    END IF;
  ELSE RAISE EXCEPTION 'unsupported provider' USING ERRCODE='22023'; END IF;
  IF data IS NULL OR NOT (data IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'model input JSON keys must be unique' USING ERRCODE='22023'; END IF;
  IF NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.request_text,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'request hash mismatch' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;

-- CREATE OR REPLACE keeps existing grants; restate the narrow ones explicitly.
REVOKE ALL ON FUNCTION fi.register_experiment(text,uuid[],jsonb,jsonb),fi.prepare_live_call(uuid,uuid,bigint,text),
  fi.provider_request_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.register_experiment(text,uuid[],jsonb,jsonb) TO fi_operator;
GRANT EXECUTE ON FUNCTION fi.prepare_live_call(uuid,uuid,bigint,text) TO fi_worker;
