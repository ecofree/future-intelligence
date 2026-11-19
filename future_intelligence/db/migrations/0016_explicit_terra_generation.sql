-- Forward-only: preserve both 0014 files and 0015 verbatim. No historical writes.
-- Pin the official Terra request, not random reproducibility or upstream weights.
CREATE FUNCTION fi.valid_terra_generation(cfg jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT coalesce(cfg->>'provider'='openai' AND cfg->>'model_id'='gpt-6.1-terra'
    AND cfg->>'prompt_sha256'='3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da'
    AND cfg#>'{parameters,generation}'='{"protocol":"fi.openai-generation.v1","reasoning":{"effort":"medium","mode":"standard"},"service_tier":"default","temperature":"OMIT_UNSUPPORTED_WITH_REASONING","top_p":"OMIT_UNSUPPORTED_WITH_REASONING","seed":"OMIT_NOT_DOCUMENTED_IN_RESPONSES"}'::jsonb,false)
$$;

CREATE OR REPLACE FUNCTION fi.native_entries_allowed(e uuid) RETURNS boolean LANGUAGE sql
SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT EXISTS(SELECT 1 FROM fi.experiment_entries WHERE experiment_id=e)
    AND NOT EXISTS(SELECT 1 FROM fi.experiment_entries c WHERE c.experiment_id=e AND (
      c.config->>'provider' IS NULL OR c.config->>'provider' NOT IN ('openai','anthropic','google')
      OR jsonb_typeof(c.config->'parameters') IS DISTINCT FROM 'object'
      OR NOT ((c.config->'parameters') ?& ARRAY['max_output_tokens','timeout_seconds','input_token_ceiling','price_quote'])
      OR ((c.config->'parameters') - ARRAY['max_output_tokens','timeout_seconds','input_token_ceiling','price_quote','generation']) IS DISTINCT FROM '{}'::jsonb
      OR (c.config - ARRAY['entry_key','provider','model_id','prompt_sha256','parameters']) IS DISTINCT FROM '{}'::jsonb
      OR (((c.config->'parameters') ? 'generation'
           OR (c.config->>'provider'='openai' AND c.config->>'model_id'='gpt-6.1-terra'))
          AND NOT fi.valid_terra_generation(c.config))
    ))
$$;

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
  IF (cfg->'parameters') ? 'generation' AND NOT fi.valid_terra_generation(cfg) THEN
    RAISE EXCEPTION 'invalid frozen generation profile' USING ERRCODE='22023'; END IF;
  IF provider_name='openai' THEN
    allowed:=ARRAY['model','instructions','input','max_output_tokens','tools','store','stream','text'];
    prompt:=b->>'instructions'; input_text:=b->>'input'; max_output:=(b->>'max_output_tokens')::integer;
    IF b->>'model' IS DISTINCT FROM cfg->>'model_id' OR b->'tools' IS DISTINCT FROM '[]'::jsonb
      OR b->'store' IS DISTINCT FROM 'false'::jsonb OR b->'stream' IS DISTINCT FROM 'false'::jsonb THEN
      RAISE EXCEPTION 'unsafe OpenAI request' USING ERRCODE='22023'; END IF;
    IF (cfg->'parameters') ? 'generation' THEN
      allowed:=allowed||ARRAY['reasoning','service_tier'];
      IF b->'reasoning' IS DISTINCT FROM cfg#>'{parameters,generation,reasoning}'
        OR b->'service_tier' IS DISTINCT FROM cfg#>'{parameters,generation,service_tier}' THEN
        RAISE EXCEPTION 'generation differs from frozen profile' USING ERRCODE='22023'; END IF;
    END IF;
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

REVOKE ALL ON FUNCTION fi.valid_terra_generation(jsonb),fi.native_entries_allowed(uuid) FROM PUBLIC;
