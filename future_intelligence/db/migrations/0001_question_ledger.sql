-- P0-01. Run only as a dedicated database owner/migrator, never an app login.
-- All public writes use narrow functions. Content rows are append-only.
CREATE SCHEMA fi;
REVOKE ALL ON SCHEMA fi FROM PUBLIC;

DO $roles$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['fi_author','fi_reviewer','fi_source_admin','fi_reader','fi_resolver'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT', r);
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r AND
                 (rolsuper OR rolcreatedb OR rolcreaterole OR rolcanlogin OR rolbypassrls)) THEN
      RAISE EXCEPTION 'unsafe pre-existing role: %', r;
    END IF;
  END LOOP;
END $roles$;

CREATE TABLE fi.sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 500),
  url text NOT NULL CHECK (url ~ '^https://[^/[:space:]]+'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.source_permissions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id uuid NOT NULL REFERENCES fi.sources(id),
  revision integer NOT NULL CHECK (revision > 0),
  allowed_uses text[] NOT NULL CHECK (allowed_uses <@ ARRAY[
    'internal_research','model_processing','model_evaluation','storage','publication']::text[]),
  valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL CHECK (valid_until > valid_from),
  license_evidence text NOT NULL CHECK (length(btrim(license_evidence)) BETWEEN 1 AND 10000),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (source_id, revision), UNIQUE(id, source_id)
);
CREATE TABLE fi.permission_revocations (
  permission_id uuid PRIMARY KEY REFERENCES fi.source_permissions(id),
  reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 10000),
  revoked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.questions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  external_key text NOT NULL UNIQUE CHECK (external_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  latest_version_id uuid,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.question_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id uuid NOT NULL REFERENCES fi.questions(id),
  revision integer NOT NULL CHECK (revision > 0),
  parent_id uuid REFERENCES fi.question_versions(id),
  spec jsonb NOT NULL,
  forecast_deadline timestamptz NOT NULL,
  outcome_not_before timestamptz NOT NULL,
  resolution_deadline timestamptz NOT NULL,
  original_timezone text NOT NULL,
  event_cluster text NOT NULL,
  snapshot_text text NOT NULL,
  snapshot_sha256 text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  created_by name NOT NULL DEFAULT session_user,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (forecast_deadline < outcome_not_before AND outcome_not_before <= resolution_deadline),
  UNIQUE(question_id, revision)
);
ALTER TABLE fi.questions ADD CONSTRAINT latest_version_fk
  FOREIGN KEY(latest_version_id) REFERENCES fi.question_versions(id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX question_cluster_idx ON fi.question_versions(event_cluster);
CREATE TABLE fi.question_sources (
  version_id uuid NOT NULL REFERENCES fi.question_versions(id),
  source_id uuid NOT NULL REFERENCES fi.sources(id),
  permission_id uuid NOT NULL,
  priority integer NOT NULL CHECK (priority > 0),
  PRIMARY KEY(version_id, source_id), UNIQUE(version_id, priority),
  FOREIGN KEY(permission_id, source_id) REFERENCES fi.source_permissions(id, source_id)
);
CREATE TABLE fi.question_states (
  version_id uuid PRIMARY KEY REFERENCES fi.question_versions(id),
  status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','FROZEN','CLOSED')),
  reviewed_at timestamptz,
  reviewed_by name,
  frozen_at timestamptz,
  closed_at timestamptz,
  CHECK ((status='DRAFT' AND reviewed_at IS NULL AND reviewed_by IS NULL AND frozen_at IS NULL AND closed_at IS NULL)
    OR (status='REVIEWED' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND frozen_at IS NULL AND closed_at IS NULL)
    OR (status='FROZEN' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND frozen_at IS NOT NULL AND closed_at IS NULL)
    OR (status='CLOSED' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND frozen_at IS NOT NULL AND closed_at IS NOT NULL))
);
CREATE TABLE fi.audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id uuid NOT NULL,
  action text NOT NULL,
  actor name NOT NULL DEFAULT session_user,
  active_role text NOT NULL DEFAULT current_setting('role'),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  detail jsonb NOT NULL
);

CREATE FUNCTION fi.reject_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, fi, pg_temp AS $$
BEGIN RAISE EXCEPTION 'append-only relation: %', TG_TABLE_NAME USING ERRCODE='55000'; END $$;
DO $triggers$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sources','source_permissions','permission_revocations',
    'question_versions','question_sources','audit_events'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.%I FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation()', t);
  END LOOP;
END $triggers$;

CREATE FUNCTION fi.audit(p_id uuid, p_action text, p_detail jsonb) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, fi, pg_temp AS $$
  INSERT INTO fi.audit_events(entity_id,action,detail) VALUES(p_id,p_action,p_detail)
$$;

CREATE FUNCTION fi.validate_spec(s jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE k text; v jsonb; edge jsonb; n numeric; previous numeric;
  required text[] := ARRAY['title','event_definition','domain','track','question_type','event_cluster',
    'original_timezone','forecast_deadline','outcome_not_before','resolution_deadline',
    'resolution_rule','data_revision_policy','unit','bin_edges'];
  deadline timestamptz; outcome_time timestamptz; resolution_time timestamptz;
BEGIN
  IF s IS NULL OR jsonb_typeof(s) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'spec must be object' USING ERRCODE='22023'; END IF;
  IF NOT (s ?& required) OR (SELECT count(*) FROM jsonb_object_keys(s)) <> cardinality(required) THEN
    RAISE EXCEPTION 'spec keys do not match v1' USING ERRCODE='22023'; END IF;
  FOR k,v IN SELECT * FROM jsonb_each(s) LOOP
    IF k <> 'bin_edges' AND (jsonb_typeof(v) <> 'string' OR length(btrim(s->>k)) NOT BETWEEN 1 AND 10000) THEN
      RAISE EXCEPTION 'invalid string field: %', k USING ERRCODE='22023'; END IF;
  END LOOP;
  IF s->>'domain' NOT IN ('MACRO','POLICY') OR s->>'track' NOT IN ('FAST','STRATEGIC')
    OR s->>'question_type' NOT IN ('binary','binned_numeric')
    OR s->>'data_revision_policy' NOT IN ('FIRST_RELEASE','LATEST_AT_RESOLUTION_DEADLINE') THEN
    RAISE EXCEPTION 'unsupported classification or revision policy' USING ERRCODE='22023'; END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name = s->>'original_timezone') THEN
    RAISE EXCEPTION 'unknown original timezone' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['forecast_deadline','outcome_not_before','resolution_deadline'] LOOP
    IF s->>k !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' THEN
      RAISE EXCEPTION 'timestamp must be ISO8601 with offset: %', k USING ERRCODE='22023'; END IF;
  END LOOP;
  deadline := (s->>'forecast_deadline')::timestamptz;
  outcome_time := (s->>'outcome_not_before')::timestamptz;
  resolution_time := (s->>'resolution_deadline')::timestamptz;
  IF NOT (deadline < outcome_time AND outcome_time <= resolution_time) THEN
    RAISE EXCEPTION 'invalid deadline ordering' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(s->'bin_edges') <> 'array' THEN
    RAISE EXCEPTION 'bin_edges must be array' USING ERRCODE='22023'; END IF;
  IF jsonb_array_length(s->'bin_edges') > 100
    OR (s->>'question_type'='binary' AND jsonb_array_length(s->'bin_edges')<>0)
    OR (s->>'question_type'='binned_numeric' AND jsonb_array_length(s->'bin_edges')=0) THEN
    RAISE EXCEPTION 'invalid bin count' USING ERRCODE='22023'; END IF;
  FOR edge IN SELECT * FROM jsonb_array_elements(s->'bin_edges') LOOP
    IF jsonb_typeof(edge) <> 'string' OR length(edge#>>'{}') > 100
      OR edge#>>'{}' !~ '^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$' THEN
      RAISE EXCEPTION 'boundary must be finite decimal string' USING ERRCODE='22023'; END IF;
    n := (edge#>>'{}')::numeric;
    IF previous IS NOT NULL AND n <= previous THEN
      RAISE EXCEPTION 'boundaries must strictly increase' USING ERRCODE='22023'; END IF;
    previous := n;
  END LOOP;
END $$;

CREATE FUNCTION fi.register_source(p_name text, p_url text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE result uuid;
BEGIN
  INSERT INTO fi.sources(name,url) VALUES(p_name,p_url) RETURNING id INTO result;
  PERFORM fi.audit(result,'SOURCE_REGISTERED',jsonb_build_object('name',p_name));
  RETURN result;
END $$;
CREATE FUNCTION fi.grant_source_permission(p_source uuid, p_uses text[], p_from timestamptz,
  p_until timestamptz, p_evidence text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE result uuid; revision_number integer;
BEGIN
  PERFORM 1 FROM fi.sources WHERE id=p_source FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'source not found' USING ERRCODE='22023'; END IF;
  IF p_uses IS NULL OR array_position(p_uses,NULL) IS NOT NULL THEN
    RAISE EXCEPTION 'allowed uses cannot be null' USING ERRCODE='22023'; END IF;
  SELECT coalesce(max(revision),0)+1 INTO revision_number FROM fi.source_permissions WHERE source_id=p_source;
  INSERT INTO fi.source_permissions(source_id,revision,allowed_uses,valid_from,valid_until,license_evidence)
    VALUES(p_source,revision_number,p_uses,p_from,p_until,p_evidence) RETURNING id INTO result;
  PERFORM fi.audit(result,'SOURCE_PERMISSION_REGISTERED',jsonb_build_object('source_id',p_source,'revision',revision_number));
  RETURN result;
END $$;
CREATE FUNCTION fi.revoke_source_permission(p_permission uuid, p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE source uuid;
BEGIN
  SELECT source_id INTO STRICT source FROM fi.source_permissions WHERE id=p_permission;
  PERFORM 1 FROM fi.sources WHERE id=source FOR UPDATE;
  INSERT INTO fi.permission_revocations(permission_id,reason) VALUES(p_permission,p_reason);
  PERFORM fi.audit(p_permission,'SOURCE_PERMISSION_REVOKED',jsonb_build_object('reason',p_reason));
END $$;

CREATE FUNCTION fi.assert_sources(p_version uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE binding record; permission fi.source_permissions; moment timestamptz;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM fi.question_sources WHERE version_id=p_version) THEN
    RAISE EXCEPTION 'question has no licensed source' USING ERRCODE='42501'; END IF;
  -- Lock in stable order; permission changes/revocations take conflicting locks.
  FOR binding IN SELECT * FROM fi.question_sources WHERE version_id=p_version ORDER BY source_id LOOP
    PERFORM 1 FROM fi.sources WHERE id=binding.source_id FOR SHARE;
    SELECT * INTO STRICT permission FROM fi.source_permissions WHERE id=binding.permission_id;
    moment := clock_timestamp();
    IF NOT (permission.allowed_uses @> ARRAY['internal_research','model_processing','model_evaluation','storage']::text[])
      OR moment < permission.valid_from OR moment >= permission.valid_until
      OR EXISTS (SELECT 1 FROM fi.permission_revocations WHERE permission_id=permission.id)
      OR EXISTS (SELECT 1 FROM fi.source_permissions WHERE source_id=permission.source_id AND revision>permission.revision) THEN
      RAISE EXCEPTION 'source permission unavailable, revoked, superseded or inadequate' USING ERRCODE='42501'; END IF;
  END LOOP;
END $$;

CREATE FUNCTION fi.create_version(p_key text,p_spec jsonb,p_permissions uuid[],p_parent uuid DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE q fi.questions; version_id uuid := gen_random_uuid(); revision_number integer;
  snapshot text; refs jsonb;
BEGIN
  PERFORM fi.validate_spec(p_spec);
  IF p_permissions IS NULL OR cardinality(p_permissions) NOT BETWEEN 1 AND 10
    OR array_position(p_permissions,NULL) IS NOT NULL THEN
    RAISE EXCEPTION 'need 1-10 explicit source permissions' USING ERRCODE='22023'; END IF;
  IF (SELECT count(*) FROM fi.source_permissions WHERE id=ANY(p_permissions)) <> cardinality(p_permissions)
    OR (SELECT count(DISTINCT source_id) FROM fi.source_permissions WHERE id=ANY(p_permissions)) <> cardinality(p_permissions) THEN
    RAISE EXCEPTION 'unknown or duplicate source permission' USING ERRCODE='22023'; END IF;
  INSERT INTO fi.questions(external_key) VALUES(p_key) ON CONFLICT(external_key) DO NOTHING;
  SELECT * INTO STRICT q FROM fi.questions WHERE external_key=p_key FOR UPDATE;
  IF q.latest_version_id IS DISTINCT FROM p_parent THEN
    RAISE EXCEPTION 'stale expected parent; reread latest version' USING ERRCODE='40001'; END IF;
  SELECT coalesce(max(revision),0)+1 INTO revision_number FROM fi.question_versions WHERE question_id=q.id;
  SELECT jsonb_agg(jsonb_build_object('permission_id',id,'priority',ord) ORDER BY ord) INTO refs
    FROM unnest(p_permissions) WITH ORDINALITY AS x(id,ord);
  snapshot := jsonb_build_object('protocol','fi.question-snapshot.v1','id',version_id,
    'question_id',q.id,'external_key',p_key,'revision',revision_number,'parent_id',p_parent,
    'spec',p_spec,'source_permissions',refs)::text;
  INSERT INTO fi.question_versions(id,question_id,revision,parent_id,spec,forecast_deadline,
    outcome_not_before,resolution_deadline,original_timezone,event_cluster,snapshot_text,snapshot_sha256)
    VALUES(version_id,q.id,revision_number,p_parent,p_spec,(p_spec->>'forecast_deadline')::timestamptz,
      (p_spec->>'outcome_not_before')::timestamptz,(p_spec->>'resolution_deadline')::timestamptz,
      p_spec->>'original_timezone',p_spec->>'event_cluster',snapshot,
      encode(sha256(convert_to(snapshot,'UTF8')),'hex'));
  INSERT INTO fi.question_sources(version_id,source_id,permission_id,priority)
    SELECT version_id,p.source_id,p.id,x.ord FROM unnest(p_permissions) WITH ORDINALITY x(id,ord)
      JOIN fi.source_permissions p ON p.id=x.id;
  INSERT INTO fi.question_states(version_id) VALUES(version_id);
  UPDATE fi.questions SET latest_version_id=version_id WHERE id=q.id;
  PERFORM fi.audit(version_id,'QUESTION_VERSION_CREATED',jsonb_build_object('revision',revision_number));
  RETURN version_id;
END $$;

CREATE FUNCTION fi.review_version(p_version uuid,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE q fi.question_versions; s fi.question_states;
BEGIN
  SELECT * INTO STRICT s FROM fi.question_states WHERE version_id=p_version FOR UPDATE;
  SELECT * INTO STRICT q FROM fi.question_versions WHERE id=p_version;
  IF s.status<>'DRAFT' THEN RAISE EXCEPTION 'review requires DRAFT' USING ERRCODE='55000'; END IF;
  IF q.created_by=session_user THEN RAISE EXCEPTION 'author cannot review own version' USING ERRCODE='42501'; END IF;
  IF p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 10000 THEN
    RAISE EXCEPTION 'review reason required' USING ERRCODE='22023'; END IF;
  PERFORM fi.assert_sources(p_version);
  IF clock_timestamp() >= q.forecast_deadline THEN
    RAISE EXCEPTION 'forecast deadline passed' USING ERRCODE='55000'; END IF;
  UPDATE fi.question_states SET status='REVIEWED',reviewed_at=clock_timestamp(),reviewed_by=session_user WHERE version_id=p_version;
  PERFORM fi.audit(p_version,'QUESTION_REVIEWED',jsonb_build_object('reason',p_reason));
END $$;
CREATE FUNCTION fi.freeze_version(p_version uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE q fi.question_versions; s fi.question_states;
BEGIN
  SELECT * INTO STRICT s FROM fi.question_states WHERE version_id=p_version FOR UPDATE;
  SELECT * INTO STRICT q FROM fi.question_versions WHERE id=p_version;
  IF s.status IN ('FROZEN','CLOSED') THEN RETURN q.snapshot_sha256; END IF;
  IF s.status<>'REVIEWED' THEN RAISE EXCEPTION 'freeze requires REVIEWED' USING ERRCODE='55000'; END IF;
  PERFORM fi.assert_sources(p_version);
  IF clock_timestamp() >= q.forecast_deadline THEN
    RAISE EXCEPTION 'forecast deadline passed' USING ERRCODE='55000'; END IF;
  UPDATE fi.question_states SET status='FROZEN',frozen_at=clock_timestamp() WHERE version_id=p_version;
  PERFORM fi.audit(p_version,'QUESTION_FROZEN',jsonb_build_object('snapshot_sha256',q.snapshot_sha256,'external_timestamp_status','NOT_REQUESTED'));
  RETURN q.snapshot_sha256;
END $$;
CREATE FUNCTION fi.close_version(p_version uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE q fi.question_versions; s fi.question_states;
BEGIN
  SELECT * INTO STRICT s FROM fi.question_states WHERE version_id=p_version FOR UPDATE;
  SELECT * INTO STRICT q FROM fi.question_versions WHERE id=p_version;
  IF s.status='CLOSED' THEN RETURN; END IF;
  IF s.status<>'FROZEN' OR clock_timestamp()<q.forecast_deadline THEN
    RAISE EXCEPTION 'close requires FROZEN and passed forecast deadline' USING ERRCODE='55000'; END IF;
  UPDATE fi.question_states SET status='CLOSED',closed_at=clock_timestamp() WHERE version_id=p_version;
  PERFORM fi.audit(p_version,'QUESTION_CLOSED','{"meaning":"submission closed; not an outcome"}'::jsonb);
END $$;

CREATE VIEW fi.question_catalog AS
  SELECT v.*, s.status,s.reviewed_at,s.reviewed_by,s.frozen_at,s.closed_at,q.external_key
  FROM fi.question_versions v JOIN fi.question_states s ON s.version_id=v.id
  JOIN fi.questions q ON q.id=v.question_id;

REVOKE ALL ON ALL TABLES IN SCHEMA fi FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fi FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fi FROM PUBLIC;
GRANT USAGE ON SCHEMA fi TO fi_author,fi_reviewer,fi_source_admin,fi_reader,fi_resolver;
GRANT SELECT ON fi.question_catalog,fi.sources,fi.source_permissions,fi.permission_revocations,
  fi.question_sources TO fi_author,fi_reviewer,fi_source_admin,fi_reader,fi_resolver;
GRANT SELECT ON fi.audit_events TO fi_reader;
GRANT EXECUTE ON FUNCTION fi.register_source(text,text),
  fi.grant_source_permission(uuid,text[],timestamptz,timestamptz,text),
  fi.revoke_source_permission(uuid,text) TO fi_source_admin;
GRANT EXECUTE ON FUNCTION fi.create_version(text,jsonb,uuid[],uuid),
  fi.freeze_version(uuid),fi.close_version(uuid) TO fi_author;
GRANT EXECUTE ON FUNCTION fi.review_version(uuid,text) TO fi_reviewer;
-- Explicit grants only: future forecast tables are NOT automatically visible to resolvers.
