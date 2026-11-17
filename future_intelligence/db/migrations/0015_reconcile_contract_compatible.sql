-- Reconcile independently committed 0014 migrations without renaming or rewriting
-- either applied file. Full filenames are migration identities (db/migrate.py).
-- The compatible 0014 sorts after explicit 0014 on fresh databases and replaces
-- its request guard. Reinstall the union so both fresh and upgraded databases
-- retain v2 exact schemas, frozen-input checks, and historical compatible v1.
-- FORMAL native-only triggers from explicit 0014 remain installed unchanged.

CREATE OR REPLACE FUNCTION fi.provider_request_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE b jsonb; data text; wanted jsonb; cfg jsonb; k integer; expected jsonb; fmt text; proto text;
  schema_def jsonb := '{"type":"object","additionalProperties":false,"properties":{"probabilities_ppm":{"type":"array","items":{"type":"integer"}},"rationale":{"type":"string"},"evidence_ids":{"type":"array","items":{"type":"string"}}},"required":["probabilities_ppm","rationale","evidence_ids"]}';
  array_def jsonb;
BEGIN
  IF NOT (NEW.request_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'request JSON keys must be unique' USING ERRCODE='22023'; END IF;
  SELECT c.config INTO STRICT cfg FROM fi.expected_tasks t JOIN fi.experiment_entries c
    ON c.experiment_id=t.experiment_id AND c.entry_key=t.entry_key WHERE t.id=NEW.task_id;
  expected:=fi.live_input(NEW.task_id);
  proto:=cfg#>>'{parameters,endpoint,protocol}';
  IF cfg->>'prompt_sha256'='3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da' THEN
    k:=(expected#>>'{answer_contract,category_count}')::integer;
    IF k IS NULL OR k NOT BETWEEN 2 AND 101 THEN
      RAISE EXCEPTION 'invalid answer space' USING ERRCODE='22023'; END IF;
    array_def:=schema_def#>'{properties,probabilities_ppm}';
    array_def:=array_def||jsonb_build_object('description',
      'Exactly '||k::text||' integer probabilities in answer_contract.categories order; each 0..1000000; total 1000000.');
    IF NEW.provider IN ('openai','google') OR (NEW.provider='openai_compatible' AND proto='openai_chat') THEN
      array_def:=array_def||jsonb_build_object('minItems',k,'maxItems',k,
        'items',jsonb_build_object('type','integer','minimum',0,'maximum',1000000));
    END IF;
    schema_def:=jsonb_set(schema_def,'{properties,probabilities_ppm}',array_def);
  END IF;
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
  IF data::jsonb IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'answer contract or input differs from frozen question' USING ERRCODE='22023'; END IF;
  IF NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.request_text,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'request hash mismatch' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION fi.provider_request_guard() FROM PUBLIC;
