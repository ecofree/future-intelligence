-- P0-07 operational scaffolding. Capture != first-release verification or live acceptance.
CREATE TABLE fi.source_captures (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), permission_id uuid NOT NULL REFERENCES fi.source_permissions,
  endpoint text NOT NULL, summary jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(), captured_by name NOT NULL DEFAULT session_user
);
CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.source_captures FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.source_captures FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation();
CREATE FUNCTION fi.admit_bls_collection(p uuid,endpoint text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE permission fi.source_permissions; source fi.sources; moment timestamptz; BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT permission FROM fi.source_permissions WHERE id=p;
  SELECT * INTO STRICT source FROM fi.sources WHERE id=permission.source_id FOR SHARE;
  moment:=clock_timestamp();
  IF endpoint IS NULL OR endpoint!~'^https://api\.bls\.gov/publicAPI/v1/timeseries/data/[A-Z0-9]{4,30}$'
    OR source.url!~'^https://api\.bls\.gov/'
    OR NOT(permission.allowed_uses @> ARRAY['internal_research','storage']::text[])
    OR moment<permission.valid_from OR moment>=permission.valid_until
    OR EXISTS(SELECT 1 FROM fi.permission_revocations WHERE permission_id=p)
    OR EXISTS(SELECT 1 FROM fi.source_permissions WHERE source_id=permission.source_id AND revision>permission.revision) THEN
    RAISE EXCEPTION 'BLS capture permission denied' USING ERRCODE='42501'; END IF;
  RETURN true;
END $$;
CREATE FUNCTION fi.store_bls_capture(p uuid,endpoint text,s jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE id uuid; keys text[]:=ARRAY['adapter','series','year','period','value_text',
  'has_target','document_sha256','observed_at','publication_time_verified','first_release_verified','requires_blind_review']; BEGIN
  PERFORM fi.admit_bls_collection(p,endpoint);
  IF s IS NULL OR jsonb_typeof(s)<>'object' OR NOT(s ?& keys) OR (SELECT count(*) FROM jsonb_object_keys(s))<>cardinality(keys)
    OR s->>'adapter' IS DISTINCT FROM 'BLS_V1_EXACT_PERIOD' OR endpoint IS DISTINCT FROM 'https://api.bls.gov/publicAPI/v1/timeseries/data/'||(s->>'series')
    OR s->'publication_time_verified' IS DISTINCT FROM 'false'::jsonb OR s->'first_release_verified' IS DISTINCT FROM 'false'::jsonb
    OR s->'requires_blind_review' IS DISTINCT FROM 'true'::jsonb OR jsonb_typeof(s->'has_target') IS DISTINCT FROM 'boolean'
    OR jsonb_typeof(s->'document_sha256') IS DISTINCT FROM 'string' OR (s->>'document_sha256')!~'^[0-9a-f]{64}$'
    OR (s->>'period')!~'^M(0[1-9]|1[0-2])$' THEN
    RAISE EXCEPTION 'invalid BLS capture contract' USING ERRCODE='22023'; END IF;
  IF ((s->>'has_target')::bool AND (jsonb_typeof(s->'value_text') IS DISTINCT FROM 'string' OR (s->>'value_text')!~'^[+-]?[0-9]+(\.[0-9]+)?$'))
    OR (NOT(s->>'has_target')::bool AND s->'value_text' IS DISTINCT FROM 'null'::jsonb) THEN
    RAISE EXCEPTION 'invalid exact-period value' USING ERRCODE='22023'; END IF;
  INSERT INTO fi.source_captures(permission_id,endpoint,summary) VALUES(p,endpoint,s) RETURNING source_captures.id INTO id;RETURN id;
END $$;
CREATE FUNCTION fi.worker_delivery_gate(e uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE b fi.batch_catalog; d timestamptz; BEGIN
  IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings WHERE experiment_id=e AND login=session_user) THEN
    RAISE EXCEPTION 'worker binding required' USING ERRCODE='42501'; END IF;
  SELECT deadline INTO STRICT d FROM fi.experiments WHERE id=e;
  IF clock_timestamp()>=d OR EXISTS(SELECT 1 FROM fi.commitment_batches WHERE experiment_id=e AND kind='FORECAST') THEN
    RETURN '{"ready":false,"reason":"PREDICTION_WINDOW_CLOSED"}'::jsonb; END IF;
  SELECT * INTO b FROM fi.batch_catalog WHERE experiment_id=e AND kind='REGISTRATION';
  IF NOT FOUND OR b.verified_upper_time IS NULL OR (b.trust_profile->>'fixture')::bool OR b.verified_upper_time>clock_timestamp() THEN
    RETURN '{"ready":false,"reason":"REGISTRATION_TIME_NOT_VERIFIED"}'::jsonb; END IF;
  RETURN '{"ready":true,"reason":"REGISTERED_NOT_PRODUCTION_ACCEPTED"}'::jsonb;
END $$;
REVOKE ALL ON fi.source_captures FROM PUBLIC;
REVOKE ALL ON FUNCTION fi.admit_bls_collection(uuid,text),fi.store_bls_capture(uuid,text,jsonb),fi.worker_delivery_gate(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fi.admit_bls_collection(uuid,text),fi.store_bls_capture(uuid,text,jsonb) TO fi_source_admin;
GRANT SELECT ON fi.source_captures TO fi_source_admin,fi_operator;
GRANT EXECUTE ON FUNCTION fi.worker_delivery_gate(uuid) TO fi_worker;
