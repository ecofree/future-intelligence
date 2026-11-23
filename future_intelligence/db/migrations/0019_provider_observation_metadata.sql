-- Forward-only observation metadata. No request, output, budget or historical
-- observation is rewritten. v1 remains valid; v2 is restricted to a registered
-- official Terra call with the existing explicit generation contract.
CREATE FUNCTION fi.valid_provider_generation_report(report jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE k text; item jsonb; values_allowed text[];
BEGIN
  IF report IS NULL OR jsonb_typeof(report) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
  IF NOT(report ?& ARRAY['reasoning_effort','reasoning_mode','service_tier'])
    OR (SELECT count(*) FROM jsonb_object_keys(report))<>3 THEN RETURN false; END IF;
  FOREACH k IN ARRAY ARRAY['reasoning_effort','reasoning_mode','service_tier'] LOOP
    item:=report->k;
    IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF NOT(item ?& ARRAY['state','value']) OR (SELECT count(*) FROM jsonb_object_keys(item))<>2
      OR jsonb_typeof(item->'state') IS DISTINCT FROM 'string'
      OR item->>'state' NOT IN ('PRESENT','MISSING','INVALID') THEN RETURN false; END IF;
    IF item->>'state'='PRESENT' THEN
      values_allowed:=CASE k
        WHEN 'reasoning_effort' THEN ARRAY['none','minimal','low','medium','high','xhigh','max']
        WHEN 'reasoning_mode' THEN ARRAY['standard','pro']
        ELSE ARRAY['auto','default','flex','priority','scale'] END;
      IF jsonb_typeof(item->'value') IS DISTINCT FROM 'string'
        OR NOT(item->>'value'=ANY(values_allowed)) THEN RETURN false; END IF;
    ELSIF item->'value' IS DISTINCT FROM 'null'::jsonb THEN
      -- Invalid upstream material is classified, never copied into the ledger.
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END $$;

CREATE FUNCTION fi.provider_observation_v2_supported() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT true
$$;

CREATE OR REPLACE FUNCTION fi.record_provider_observation(t uuid,a uuid,f bigint,obs jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE att fi.task_attempts; old fi.provider_observations; digest text; k text; q fi.expected_tasks;
  allowed text[]:=ARRAY['protocol','status','http_status','response_sha256','model_reported','request_id','usage','output'];
  public_ids text[]:=ARRAY['model_reported','request_id'];
BEGIN
  PERFORM fi.ledger_lock(t);
  SELECT * INTO STRICT att FROM fi.task_attempts WHERE id=a AND task_id=t;
  IF att.owner<>session_user OR att.fence IS DISTINCT FROM f OR NOT EXISTS(SELECT 1 FROM fi.provider_requests WHERE attempt_id=a) THEN
    RAISE EXCEPTION 'foreign or unregistered call' USING ERRCODE='42501'; END IF;
  IF obs->>'protocol'='fi.provider-observation.v2' THEN
    allowed:=allowed||ARRAY['response_id','generation_reported'];
    public_ids:=public_ids||ARRAY['response_id'];
  END IF;
  IF obs IS NULL OR jsonb_typeof(obs)<>'object' OR NOT(obs ?& allowed)
    OR (SELECT count(*) FROM jsonb_object_keys(obs))<>cardinality(allowed) OR octet_length(obs::text)>32000
    OR coalesce(obs->>'protocol','') NOT IN ('fi.provider-observation.v1','fi.provider-observation.v2')
    OR coalesce(obs->>'status','') NOT IN ('OK','INVALID_OUTPUT','INCOMPLETE','REFUSED','HTTP_ERROR','TIMEOUT','TRANSPORT_ERROR','RESPONSE_TOO_LARGE') THEN
    RAISE EXCEPTION 'invalid sanitized observation' USING ERRCODE='22023'; END IF;
  IF obs->>'protocol'='fi.provider-observation.v2' THEN
    IF NOT EXISTS(SELECT 1 FROM fi.provider_requests r
      JOIN fi.expected_tasks task ON task.id=r.task_id
      JOIN fi.experiment_entries entry ON entry.experiment_id=task.experiment_id AND entry.entry_key=task.entry_key
      WHERE r.attempt_id=a AND r.task_id=t AND r.provider='openai' AND r.model_requested='gpt-6.1-terra'
        AND fi.valid_terra_generation(entry.config)) THEN
      RAISE EXCEPTION 'observation v2 requires frozen official Terra request' USING ERRCODE='22023'; END IF;
    IF NOT fi.valid_provider_generation_report(obs->'generation_reported') THEN
      RAISE EXCEPTION 'invalid sanitized generation report' USING ERRCODE='22023'; END IF;
  END IF;
  IF obs->>'response_sha256' IS NOT NULL AND (obs->>'response_sha256')!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid response digest' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY public_ids LOOP
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

REVOKE ALL ON FUNCTION fi.valid_provider_generation_report(jsonb) FROM PUBLIC,fi_worker;
REVOKE ALL ON FUNCTION fi.provider_observation_v2_supported() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.provider_observation_v2_supported() TO fi_worker;
