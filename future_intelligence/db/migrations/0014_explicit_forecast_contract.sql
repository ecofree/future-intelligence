-- Forward-only correction of the Grok test's category-count ambiguity.
-- No new tables, no record rewrites, no change to scoring or retry semantics.
-- v1 remains replayable; frozen prompt hash selects the v2 input contract.
CREATE FUNCTION fi.answer_contract_v2(s jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE categories jsonb:='[]'; edges jsonb; k integer; i integer; lo text; hi text; label text;
BEGIN
  PERFORM fi.validate_spec(s);
  IF s->>'question_type'='binary' THEN
    categories:='[{"index":0,"label":"No"},{"index":1,"label":"Yes"}]'::jsonb;
  ELSE
    edges:=s->'bin_edges'; k:=jsonb_array_length(edges)+1;
    FOR i IN 0..k-1 LOOP
      lo:=CASE WHEN i=0 THEN NULL ELSE edges->>(i-1) END;
      hi:=CASE WHEN i=k-1 THEN NULL ELSE edges->>i END;
      label:=CASE WHEN lo IS NULL THEN 'x < '||hi
                  WHEN hi IS NULL THEN 'x >= '||lo ELSE lo||' <= x < '||hi END;
      categories:=categories||jsonb_build_array(jsonb_build_object(
        'index',i,'label',label,'lower',lo,'upper',hi,
        'lower_inclusive',lo IS NOT NULL,'upper_inclusive',false));
    END LOOP;
  END IF;
  RETURN jsonb_build_object('protocol','fi.answer-contract.v2',
    'category_count',jsonb_array_length(categories),'probability_total_ppm',1000000,
    'unit',s->>'unit','categories',categories);
END $$;

CREATE OR REPLACE FUNCTION fi.live_input(t uuid) RETURNS jsonb LANGUAGE sql
SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT jsonb_build_object('protocol','fi.model-input.v1','question_spec',v.spec,
    'evidence',p.packet_text::jsonb,'evidence_sha256',p.packet_sha256)
    || CASE WHEN c.config->>'prompt_sha256'='3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da'
       THEN jsonb_build_object('protocol','fi.model-input.v2','answer_contract',fi.answer_contract_v2(v.spec))
       ELSE '{}'::jsonb END
  FROM fi.expected_tasks q JOIN fi.question_versions v ON v.id=q.question_version_id
    JOIN fi.evidence_packets p ON p.experiment_id=q.experiment_id AND p.question_version_id=q.question_version_id
    JOIN fi.experiment_entries c ON c.experiment_id=q.experiment_id AND c.entry_key=q.entry_key
  WHERE q.id=t
$$;

CREATE OR REPLACE FUNCTION fi.provider_request_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE b jsonb; data text; wanted jsonb; cfg jsonb; k integer; expected jsonb;
  schema_def jsonb := '{"type":"object","additionalProperties":false,"properties":{"probabilities_ppm":{"type":"array","items":{"type":"integer"}},"rationale":{"type":"string"},"evidence_ids":{"type":"array","items":{"type":"string"}}},"required":["probabilities_ppm","rationale","evidence_ids"]}';
  array_def jsonb;
BEGIN
  IF NOT (NEW.request_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'request JSON keys must be unique' USING ERRCODE='22023'; END IF;
  SELECT c.config INTO STRICT cfg FROM fi.expected_tasks t JOIN fi.experiment_entries c
    ON c.experiment_id=t.experiment_id AND c.entry_key=t.entry_key WHERE t.id=NEW.task_id;
  expected:=fi.live_input(NEW.task_id);
  IF cfg->>'prompt_sha256'='3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da' THEN
    k:=(expected#>>'{answer_contract,category_count}')::integer;
    IF k IS NULL OR k NOT BETWEEN 2 AND 101 THEN
      RAISE EXCEPTION 'invalid answer space' USING ERRCODE='22023'; END IF;
    array_def:=schema_def#>'{properties,probabilities_ppm}';
    array_def:=array_def||jsonb_build_object('description',
      'Exactly '||k::text||' integer probabilities in answer_contract.categories order; each 0..1000000; total 1000000.');
    IF NEW.provider IN ('openai','google') THEN
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
  ELSE RAISE EXCEPTION 'unsupported provider' USING ERRCODE='22023'; END IF;
  IF data IS NULL OR NOT (data IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'model input JSON keys must be unique' USING ERRCODE='22023'; END IF;
  IF data::jsonb IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'answer contract or input differs from frozen question' USING ERRCODE='22023'; END IF;
  IF NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.request_text,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'request hash mismatch' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;

-- This is configuration eligibility, NOT cryptographic attestation of an upstream.
-- Main has no compatible/relay adapter. An endpoint label cannot grant FORMAL.
CREATE FUNCTION fi.native_entries_allowed(e uuid) RETURNS boolean LANGUAGE sql
SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT EXISTS(SELECT 1 FROM fi.experiment_entries WHERE experiment_id=e)
    AND NOT EXISTS(SELECT 1 FROM fi.experiment_entries c WHERE c.experiment_id=e AND (
      c.config->>'provider' IS NULL OR c.config->>'provider' NOT IN ('openai','anthropic','google')
      OR jsonb_typeof(c.config->'parameters') IS DISTINCT FROM 'object'
      OR NOT ((c.config->'parameters') ?& ARRAY['max_output_tokens','timeout_seconds','input_token_ceiling','price_quote'])
      OR ((c.config->'parameters') - ARRAY['max_output_tokens','timeout_seconds','input_token_ceiling','price_quote']) IS DISTINCT FROM '{}'::jsonb
      OR (c.config - ARRAY['entry_key','provider','model_id','prompt_sha256','parameters']) IS DISTINCT FROM '{}'::jsonb
    ))
$$;

CREATE FUNCTION fi.formal_native_entry_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE e uuid; m text;
BEGIN
  IF TG_TABLE_NAME='provider_requests' THEN
    SELECT experiment_id INTO STRICT e FROM fi.expected_tasks WHERE id=NEW.task_id;
    SELECT mode INTO m FROM fi.operation_policies WHERE experiment_id=e;
  ELSIF TG_TABLE_NAME='operation_policies' THEN
    e:=NEW.experiment_id; m:=NEW.mode;
  ELSE
    e:=NEW.experiment_id;
    SELECT mode INTO m FROM fi.operation_policies WHERE experiment_id=e;
  END IF;
  IF m='FORMAL' AND EXISTS(SELECT 1 FROM fi.experiments WHERE id=e AND spec->>'run_kind'='LIVE')
    AND NOT fi.native_entries_allowed(e) THEN
    RAISE EXCEPTION 'FORMAL_REQUIRES_NATIVE_ENDPOINT' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER formal_native_entries BEFORE INSERT ON fi.operation_policies
  FOR EACH ROW EXECUTE FUNCTION fi.formal_native_entry_guard();
CREATE TRIGGER formal_native_entries BEFORE INSERT ON fi.commitment_batches
  FOR EACH ROW EXECUTE FUNCTION fi.formal_native_entry_guard();
CREATE TRIGGER formal_native_entries BEFORE INSERT ON fi.provider_requests
  FOR EACH ROW EXECUTE FUNCTION fi.formal_native_entry_guard();
REVOKE ALL ON FUNCTION fi.answer_contract_v2(jsonb),fi.native_entries_allowed(uuid),
  fi.formal_native_entry_guard(),fi.live_input(uuid),fi.provider_request_guard() FROM PUBLIC;
