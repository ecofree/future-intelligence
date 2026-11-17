-- Forward-only repair: preserve runner-kind authentication precedence and
-- enforce the common output contract independently of the Python adapters.
CREATE OR REPLACE FUNCTION fi.claim_task(e uuid,p_kind text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE kind text; BEGIN
  PERFORM fi.ledger_rc();
  SELECT spec->>'run_kind' INTO STRICT kind FROM fi.experiments WHERE id=e;
  IF p_kind IS DISTINCT FROM kind THEN
    RAISE EXCEPTION 'runner kind mismatch' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings WHERE experiment_id=e AND login=session_user) THEN
    RAISE EXCEPTION 'worker has no entry binding' USING ERRCODE='42501'; END IF;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF kind='LIVE' AND NOT EXISTS(SELECT 1 FROM fi.evidence_seals WHERE experiment_id=e) THEN
    RAISE EXCEPTION 'LIVE requires sealed evidence' USING ERRCODE='55000'; END IF;
  RETURN fi.claim_task_p002(e,p_kind);
END $$;

CREATE FUNCTION fi.provider_request_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE b jsonb; data text; wanted jsonb;
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
  ELSE RAISE EXCEPTION 'unsupported provider' USING ERRCODE='22023'; END IF;
  IF data IS NULL OR NOT (data IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'model input JSON keys must be unique' USING ERRCODE='22023'; END IF;
  IF NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.request_text,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'request hash mismatch' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_request_contract BEFORE INSERT ON fi.provider_requests
  FOR EACH ROW EXECUTE FUNCTION fi.provider_request_guard();

CREATE FUNCTION fi.provider_observation_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE probs jsonb; p jsonb; total bigint:=0; k integer; ids jsonb;
BEGIN
  IF NEW.observation->>'status'='OK' THEN
    SELECT CASE WHEN v.spec->>'question_type'='binary' THEN 2 ELSE jsonb_array_length(v.spec->'bin_edges')+1 END
      INTO STRICT k FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id
      JOIN fi.question_versions v ON v.id=t.question_version_id WHERE a.id=NEW.attempt_id;
    probs:=NEW.observation#>'{output,probabilities_ppm}';
    IF jsonb_typeof(probs) IS DISTINCT FROM 'array' OR jsonb_array_length(probs)<>k THEN
      RAISE EXCEPTION 'invalid observed probability length' USING ERRCODE='22023'; END IF;
    FOR p IN SELECT * FROM jsonb_array_elements(probs) LOOP
      IF jsonb_typeof(p)<>'number' OR p::text!~'^[0-9]{1,7}$' OR p::text::numeric>1000000 THEN
        RAISE EXCEPTION 'invalid observed probability' USING ERRCODE='22023'; END IF;
      total:=total+p::text::bigint;
    END LOOP;
    IF total<>1000000 THEN RAISE EXCEPTION 'invalid observed probability sum' USING ERRCODE='22023'; END IF;
    ids:=NEW.observation#>'{output,evidence_ids}';
    IF (SELECT count(DISTINCT x) FROM jsonb_array_elements(ids) x)<>jsonb_array_length(ids) THEN
      RAISE EXCEPTION 'duplicate evidence citation' USING ERRCODE='22023'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_observation_contract BEFORE INSERT ON fi.provider_observations
  FOR EACH ROW EXECUTE FUNCTION fi.provider_observation_guard();
REVOKE ALL ON FUNCTION fi.provider_request_guard(),fi.provider_observation_guard(),fi.claim_task(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.claim_task(uuid,text) TO fi_worker;
