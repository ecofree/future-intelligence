-- P0-02: short database transactions only; no provider I/O in a transaction.
-- Never edit 0001/0002. Result receipts below are NOT externally timestamped forecasts.
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['fi_operator','fi_worker','fi_dispatcher'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',r);
    ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r AND
      (rolsuper OR rolcreatedb OR rolcreaterole OR rolcanlogin OR rolbypassrls)) THEN
      RAISE EXCEPTION 'unsafe pre-existing role: %',r;
    END IF;
  END LOOP;
END $$;

CREATE TABLE fi.experiments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  external_key text UNIQUE NOT NULL CHECK(external_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  spec jsonb NOT NULL,
  request_sha256 text NOT NULL,
  manifest_text text NOT NULL,
  manifest_sha256 text NOT NULL,
  deadline timestamptz NOT NULL,
  created_by name NOT NULL DEFAULT session_user,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.experiment_entries (
  experiment_id uuid REFERENCES fi.experiments(id),
  entry_key text NOT NULL,
  config jsonb NOT NULL,
  config_sha256 text NOT NULL,
  PRIMARY KEY(experiment_id,entry_key)
);
CREATE TABLE fi.worker_bindings (
  experiment_id uuid NOT NULL,
  entry_key text NOT NULL,
  login name NOT NULL,
  PRIMARY KEY(experiment_id,login),
  FOREIGN KEY(experiment_id,entry_key) REFERENCES fi.experiment_entries
);
CREATE TABLE fi.expected_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES fi.experiments(id),
  question_version_id uuid NOT NULL REFERENCES fi.question_versions(id),
  entry_key text NOT NULL,
  UNIQUE(experiment_id,question_version_id,entry_key),
  FOREIGN KEY(experiment_id,entry_key) REFERENCES fi.experiment_entries
);
CREATE TABLE fi.task_states (
  task_id uuid PRIMARY KEY REFERENCES fi.expected_tasks(id),
  status text NOT NULL DEFAULT 'QUEUED' CHECK(status IN
    ('QUEUED','RUNNING','UNCERTAIN','SUCCEEDED','FAILED','TIMED_OUT','CANCELLED')),
  attempt_no integer NOT NULL DEFAULT 0,
  fence bigint NOT NULL DEFAULT 0,
  active_attempt uuid,
  owner name,
  lease_until timestamptz,
  last_error text,
  completed_at timestamptz
);
CREATE TABLE fi.task_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL REFERENCES fi.expected_tasks(id),
  ordinal integer NOT NULL,
  fence bigint NOT NULL,
  owner name NOT NULL DEFAULT session_user,
  claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  call_started_at timestamptz,
  finished_at timestamptz,
  state text NOT NULL CHECK(state IN
    ('CLAIMED','CALL_STARTED','SUCCEEDED','FAILED','TIMED_OUT','UNCERTAIN','CANCELLED')),
  receipt_sha256 text,
  UNIQUE(task_id,ordinal), UNIQUE(task_id,fence)
);
ALTER TABLE fi.task_states ADD FOREIGN KEY(active_attempt) REFERENCES fi.task_attempts(id);
CREATE TABLE fi.budget_accounts (
  experiment_id uuid PRIMARY KEY REFERENCES fi.experiments(id),
  spent_microusd bigint NOT NULL DEFAULT 0 CHECK(spent_microusd>=0),
  reserved_microusd bigint NOT NULL DEFAULT 0 CHECK(reserved_microusd>=0)
);
CREATE TABLE fi.budget_reservations (
  attempt_id uuid PRIMARY KEY REFERENCES fi.task_attempts(id),
  reserved_microusd bigint NOT NULL CHECK(reserved_microusd>0),
  actual_microusd bigint CHECK(actual_microusd>=0),
  state text NOT NULL DEFAULT 'RESERVED' CHECK(state IN ('RESERVED','HELD_UNKNOWN','SETTLED')),
  evidence text,
  CHECK((state='SETTLED')=(actual_microusd IS NOT NULL))
);
CREATE TABLE fi.task_submissions (
  task_id uuid PRIMARY KEY REFERENCES fi.expected_tasks(id),
  attempt_id uuid UNIQUE NOT NULL REFERENCES fi.task_attempts(id),
  probabilities_ppm jsonb NOT NULL,
  response_sha256 text NOT NULL CHECK(response_sha256 ~ '^[0-9a-f]{64}$'),
  snapshot_text text NOT NULL,
  snapshot_sha256 text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.outbox_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_key text NOT NULL UNIQUE,
  experiment_id uuid NOT NULL REFERENCES fi.experiments(id),
  task_id uuid REFERENCES fi.expected_tasks(id),
  kind text NOT NULL,
  detail jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.outbox_delivery (
  event_id bigint PRIMARY KEY REFERENCES fi.outbox_events(id),
  owner name,
  fence bigint NOT NULL DEFAULT 0,
  lease_until timestamptz,
  acknowledged_at timestamptz
);
CREATE INDEX task_queue_idx ON fi.task_states(status,task_id);
CREATE INDEX task_experiment_idx ON fi.expected_tasks(experiment_id,entry_key);
CREATE INDEX outbox_pending_idx ON fi.outbox_delivery(event_id) WHERE acknowledged_at IS NULL;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['experiments','experiment_entries','worker_bindings',
    'expected_tasks','task_submissions','outbox_events'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.%I FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation()',t);
  END LOOP;
END $$;

CREATE FUNCTION fi.ledger_rc() RETURNS void LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  IF current_setting('transaction_isolation')<>'read committed' THEN
    RAISE EXCEPTION 'ledger requires READ COMMITTED' USING ERRCODE='25000'; END IF;
END $$;
CREATE FUNCTION fi.ledger_emit(e uuid,t uuid,k text,d jsonb) RETURNS void LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE n bigint; BEGIN
  INSERT INTO fi.outbox_events(event_key,experiment_id,task_id,kind,detail)
    VALUES(gen_random_uuid()::text,e,t,k,d) RETURNING id INTO n;
  INSERT INTO fi.outbox_delivery(event_id) VALUES(n);
  PERFORM fi.audit(coalesce(t,e),k,d);
END $$;

CREATE FUNCTION fi.register_experiment(p_key text,p_versions uuid[],p_entries jsonb,p_spec jsonb)
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
      OR (p_spec->>'run_kind'='LIVE' AND item->>'provider' NOT IN ('openai','anthropic','google')) THEN
      RAISE EXCEPTION 'invalid entry identity or kind' USING ERRCODE='22023'; END IF;
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

CREATE FUNCTION fi.bind_worker(e uuid,k text,p_login name) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=p_login AND rolcanlogin AND NOT rolsuper)
    OR NOT pg_has_role(p_login,'fi_worker','MEMBER') OR pg_has_role(p_login,'fi_operator','MEMBER')
    OR pg_has_role(p_login,'fi_resolver','MEMBER') THEN
    RAISE EXCEPTION 'require isolated authenticated worker login' USING ERRCODE='42501'; END IF;
  INSERT INTO fi.worker_bindings VALUES(e,k,p_login) ON CONFLICT DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings WHERE experiment_id=e AND login=p_login AND entry_key=k) THEN
    RAISE EXCEPTION 'worker already bound to another entry' USING ERRCODE='42501'; END IF;
END $$;

-- Every mutator locks budget -> task. Lock waits never extend a submitted lease.
CREATE FUNCTION fi.ledger_lock(t uuid) RETURNS fi.task_states LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE e uuid; s fi.task_states; BEGIN
  PERFORM fi.ledger_rc();
  SELECT experiment_id INTO STRICT e FROM fi.expected_tasks WHERE id=t;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  SELECT * INTO STRICT s FROM fi.task_states WHERE task_id=t FOR UPDATE;
  RETURN s;
END $$;
CREATE FUNCTION fi.ledger_active(t uuid,a uuid,f bigint) RETURNS fi.task_states LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE s fi.task_states; d timestamptz; BEGIN
  s:=fi.ledger_lock(t);
  SELECT e.deadline INTO d FROM fi.expected_tasks x JOIN fi.experiments e ON e.id=x.experiment_id WHERE x.id=t;
  IF a IS NULL OR f IS NULL OR s.active_attempt IS DISTINCT FROM a OR s.fence IS DISTINCT FROM f
    OR s.owner IS DISTINCT FROM session_user OR s.status<>'RUNNING'
    OR clock_timestamp()>=s.lease_until OR clock_timestamp()>=d THEN
    RAISE EXCEPTION 'stale, expired or foreign task lease' USING ERRCODE='55000'; END IF;
  RETURN s;
END $$;
CREATE FUNCTION fi.ledger_settle(a uuid,actual bigint,evidence_text text) RETURNS void LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE r fi.budget_reservations; e uuid; BEGIN
  SELECT * INTO STRICT r FROM fi.budget_reservations WHERE attempt_id=a FOR UPDATE;
  SELECT x.experiment_id INTO e FROM fi.task_attempts z JOIN fi.expected_tasks x ON x.id=z.task_id WHERE z.id=a;
  IF actual IS NULL THEN
    UPDATE fi.budget_reservations SET state='HELD_UNKNOWN' WHERE attempt_id=a AND state='RESERVED';
    RETURN;
  END IF;
  IF actual<0 OR actual>1000000000000 THEN RAISE EXCEPTION 'invalid actual cost' USING ERRCODE='22023'; END IF;
  IF r.state='SETTLED' THEN
    IF r.actual_microusd<>actual THEN RAISE EXCEPTION 'cost already settled' USING ERRCODE='55000'; END IF;
    RETURN;
  END IF;
  -- Reality can exceed reservation. Record overrun honestly and block future claims.
  UPDATE fi.budget_accounts SET reserved_microusd=reserved_microusd-r.reserved_microusd,
    spent_microusd=spent_microusd+actual WHERE experiment_id=e;
  UPDATE fi.budget_reservations SET actual_microusd=actual,state='SETTLED',evidence=evidence_text WHERE attempt_id=a;
END $$;

CREATE FUNCTION fi.claim_task(e uuid,p_kind text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; b fi.budget_accounts; t fi.expected_tasks; s fi.task_states;
  a uuid:=gen_random_uuid(); cap bigint; until_time timestamptz; cfg jsonb;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  IF p_kind IS DISTINCT FROM x.spec->>'run_kind' THEN
    RAISE EXCEPTION 'runner kind mismatch' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(SELECT 1 FROM fi.worker_bindings WHERE experiment_id=e AND login=session_user) THEN
    RAISE EXCEPTION 'worker has no entry binding' USING ERRCODE='42501'; END IF;
  SELECT * INTO STRICT b FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  IF clock_timestamp()>=x.deadline THEN RETURN NULL; END IF;
  cap:=(x.spec->>'attempt_cap_microusd')::bigint;
  IF b.spent_microusd+b.reserved_microusd+cap>(x.spec->>'budget_microusd')::bigint THEN RETURN NULL; END IF;
  LOOP
  SELECT st.* INTO s FROM fi.task_states st JOIN fi.expected_tasks q ON q.id=st.task_id
    JOIN fi.worker_bindings w ON w.experiment_id=q.experiment_id AND w.entry_key=q.entry_key
    WHERE q.experiment_id=e AND w.login=session_user AND st.status='QUEUED'
      AND st.attempt_no<(x.spec->>'max_attempts')::integer
    ORDER BY st.task_id FOR UPDATE OF st SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO STRICT t FROM fi.expected_tasks WHERE id=s.task_id;
  -- A bad source affects this task, not the other tasks in the experiment.
  BEGIN
    PERFORM fi.assert_sources(t.question_version_id);
  EXCEPTION WHEN insufficient_privilege THEN
    UPDATE fi.task_states SET status='FAILED',last_error='SOURCE_PERMISSION',completed_at=clock_timestamp() WHERE task_id=t.id;
    PERFORM fi.ledger_emit(e,t.id,'TASK_FAILED','{"reason":"SOURCE_PERMISSION"}');
    CONTINUE;
  END;
  IF clock_timestamp()>=x.deadline THEN RETURN NULL; END IF;
  until_time:=least(x.deadline,clock_timestamp()+make_interval(secs=>(x.spec->>'lease_seconds')::integer));
  INSERT INTO fi.task_attempts(id,task_id,ordinal,fence,state)
    VALUES(a,t.id,s.attempt_no+1,s.fence+1,'CLAIMED');
  INSERT INTO fi.budget_reservations(attempt_id,reserved_microusd) VALUES(a,cap);
  UPDATE fi.budget_accounts SET reserved_microusd=reserved_microusd+cap WHERE experiment_id=e;
  UPDATE fi.task_states SET status='RUNNING',attempt_no=attempt_no+1,fence=fence+1,
    active_attempt=a,owner=session_user,lease_until=until_time,last_error=NULL WHERE task_id=t.id;
  PERFORM fi.ledger_emit(e,t.id,'TASK_CLAIMED',jsonb_build_object('attempt_id',a,'fence',s.fence+1));
  SELECT config INTO cfg FROM fi.experiment_entries WHERE experiment_id=e AND entry_key=t.entry_key;
  RETURN jsonb_build_object('task_id',t.id,'attempt_id',a,'fence',s.fence+1,'lease_until',until_time,
    'question_version_id',t.question_version_id,'entry',cfg,'run_kind',x.spec->>'run_kind','cap_microusd',cap,
    'question_spec',(SELECT spec FROM fi.question_versions WHERE id=t.question_version_id));
  END LOOP;
END $$;

CREATE FUNCTION fi.heartbeat_task(t uuid,a uuid,f bigint) RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE x fi.experiments; u timestamptz; BEGIN
  PERFORM fi.ledger_active(t,a,f);
  SELECT e.* INTO x FROM fi.expected_tasks q JOIN fi.experiments e ON e.id=q.experiment_id WHERE q.id=t;
  u:=least(x.deadline,clock_timestamp()+make_interval(secs=>(x.spec->>'lease_seconds')::integer));
  UPDATE fi.task_states SET lease_until=u WHERE task_id=t;
  RETURN u;
END $$;
CREATE FUNCTION fi.start_task_call(t uuid,a uuid,f bigint) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE q fi.expected_tasks; BEGIN
  PERFORM fi.ledger_active(t,a,f);
  SELECT * INTO q FROM fi.expected_tasks WHERE id=t;
  PERFORM fi.assert_sources(q.question_version_id);
  PERFORM fi.ledger_active(t,a,f); -- Recheck after source lock waits.
  IF EXISTS(SELECT 1 FROM fi.task_attempts WHERE id=a AND call_started_at IS NOT NULL) THEN RETURN false; END IF;
  UPDATE fi.task_attempts SET state='CALL_STARTED',call_started_at=clock_timestamp() WHERE id=a;
  PERFORM fi.ledger_emit(q.experiment_id,t,'CALL_STARTED',jsonb_build_object('attempt_id',a));
  RETURN true;
END $$;

CREATE FUNCTION fi.finish_task(t uuid,a uuid,f bigint,probs jsonb,response_hash text,
  actual bigint,error_code text,retryable boolean) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE s fi.task_states; att fi.task_attempts; q fi.expected_tasks; x fi.experiments;
  receipt text; snapshot text; result_hash text; k integer; n numeric; total bigint:=0;
  p jsonb; next_status text; ok boolean:=probs IS NOT NULL;
BEGIN
  s:=fi.ledger_lock(t);
  SELECT * INTO STRICT att FROM fi.task_attempts WHERE id=a AND task_id=t;
  IF f IS NULL OR att.fence<>f OR att.owner<>session_user THEN
    RAISE EXCEPTION 'foreign attempt' USING ERRCODE='42501'; END IF;
  IF retryable IS NULL OR (actual IS NOT NULL AND (actual<0 OR actual>1000000000000)) THEN
    RAISE EXCEPTION 'invalid completion fields' USING ERRCODE='22023'; END IF;
  receipt:=encode(sha256(convert_to(jsonb_build_object('probs',probs,'hash',response_hash,
    'cost',actual,'error',error_code,'retryable',retryable)::text,'UTF8')),'hex');
  IF att.receipt_sha256 IS NOT NULL THEN
    IF att.receipt_sha256<>receipt THEN RAISE EXCEPTION 'conflicting completion retry' USING ERRCODE='22023'; END IF;
    RETURN att.receipt_sha256;
  END IF;
  PERFORM fi.ledger_active(t,a,f);
  SELECT * INTO q FROM fi.expected_tasks WHERE id=t;
  SELECT * INTO x FROM fi.experiments WHERE id=q.experiment_id;
  IF ok THEN
    IF att.call_started_at IS NULL OR error_code IS NOT NULL OR retryable OR response_hash IS NULL
      OR response_hash!~'^[0-9a-f]{64}$' OR jsonb_typeof(probs)<>'array' THEN
      RAISE EXCEPTION 'invalid successful completion' USING ERRCODE='22023'; END IF;
    SELECT CASE WHEN spec->>'question_type'='binary' THEN 2 ELSE jsonb_array_length(spec->'bin_edges')+1 END
      INTO k FROM fi.question_versions WHERE id=q.question_version_id;
    IF jsonb_array_length(probs)<>k THEN RAISE EXCEPTION 'probability length mismatch' USING ERRCODE='22023'; END IF;
    FOR p IN SELECT * FROM jsonb_array_elements(probs) LOOP
      IF jsonb_typeof(p)<>'number' OR p::text!~'^[0-9]{1,7}$' THEN
        RAISE EXCEPTION 'probability must be integer ppm' USING ERRCODE='22023'; END IF;
      n:=p::text::numeric;
      IF n>1000000 THEN RAISE EXCEPTION 'probability out of bounds' USING ERRCODE='22023'; END IF;
      total:=total+n::bigint;
    END LOOP;
    IF total<>1000000 THEN RAISE EXCEPTION 'probabilities must sum to 1000000' USING ERRCODE='22023'; END IF;
    PERFORM fi.assert_sources(q.question_version_id);
    PERFORM fi.ledger_active(t,a,f);
    next_status:='SUCCEEDED';
    snapshot:=jsonb_build_object('protocol','fi.task-receipt.v1','task_id',t,'attempt_id',a,
      'run_kind',x.spec->>'run_kind','probabilities_ppm',probs,'response_sha256',response_hash,
      'external_timestamp_status','NOT_REQUESTED')::text;
    result_hash:=encode(sha256(convert_to(snapshot,'UTF8')),'hex');
    INSERT INTO fi.task_submissions VALUES(t,a,probs,response_hash,snapshot,result_hash,clock_timestamp());
  ELSE
    IF error_code IS NULL OR error_code!~'^[A-Z][A-Z0-9_]{0,63}$' OR response_hash IS NOT NULL THEN
      RAISE EXCEPTION 'failure requires sanitized error code only' USING ERRCODE='22023'; END IF;
    IF att.call_started_at IS NULL THEN
      IF actual IS NOT NULL AND actual<>0 THEN RAISE EXCEPTION 'cost before call' USING ERRCODE='22023'; END IF;
      actual:=0;
    END IF;
    next_status:=CASE WHEN actual IS NULL THEN 'UNCERTAIN'
      WHEN retryable AND s.attempt_no<(x.spec->>'max_attempts')::integer THEN 'QUEUED' ELSE 'FAILED' END;
  END IF;
  PERFORM fi.ledger_settle(a,actual,'worker receipt');
  UPDATE fi.task_attempts SET state=CASE WHEN ok THEN 'SUCCEEDED' WHEN actual IS NULL THEN 'UNCERTAIN' ELSE 'FAILED' END,
    receipt_sha256=receipt,finished_at=clock_timestamp() WHERE id=a;
  UPDATE fi.task_states SET status=next_status,lease_until=NULL,last_error=error_code,
    completed_at=CASE WHEN next_status IN ('SUCCEEDED','FAILED') THEN clock_timestamp() ELSE NULL END WHERE task_id=t;
  PERFORM fi.ledger_emit(q.experiment_id,t,'TASK_'||next_status,jsonb_build_object('attempt_id',a,'cost_known',actual IS NOT NULL));
  RETURN receipt;
END $$;

CREATE FUNCTION fi.recover_tasks(e uuid,p_limit integer DEFAULT 100) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; s fi.task_states; att fi.task_attempts; ns text; actual bigint; count_done integer:=0;
BEGIN
  PERFORM fi.ledger_rc();
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'invalid recovery limit' USING ERRCODE='22023'; END IF;
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  FOR s IN SELECT st.* FROM fi.task_states st JOIN fi.expected_tasks q ON q.id=st.task_id
    WHERE q.experiment_id=e AND ((st.status='RUNNING' AND st.lease_until<=clock_timestamp())
      OR (st.status IN ('QUEUED','UNCERTAIN') AND x.deadline<=clock_timestamp()))
    ORDER BY st.task_id FOR UPDATE OF st SKIP LOCKED LIMIT p_limit LOOP
    IF s.status='RUNNING' THEN
      SELECT * INTO STRICT att FROM fi.task_attempts WHERE id=s.active_attempt;
      actual:=CASE WHEN att.call_started_at IS NULL THEN 0 ELSE NULL END;
      PERFORM fi.ledger_settle(att.id,actual,'expired execution lease');
      UPDATE fi.task_attempts SET state=CASE WHEN actual IS NULL THEN 'UNCERTAIN' ELSE 'TIMED_OUT' END,
        finished_at=clock_timestamp() WHERE id=att.id;
      ns:=CASE WHEN clock_timestamp()>=x.deadline THEN 'TIMED_OUT' WHEN actual IS NULL THEN 'UNCERTAIN'
        WHEN s.attempt_no<(x.spec->>'max_attempts')::integer THEN 'QUEUED' ELSE 'TIMED_OUT' END;
    ELSE ns:='TIMED_OUT'; END IF;
    UPDATE fi.task_states SET status=ns,lease_until=NULL,last_error='LEASE_OR_DEADLINE_EXPIRED',
      completed_at=CASE WHEN ns='TIMED_OUT' THEN clock_timestamp() ELSE NULL END WHERE task_id=s.task_id;
    PERFORM fi.ledger_emit(e,s.task_id,'TASK_'||ns,jsonb_build_object('reason','LEASE_OR_DEADLINE_EXPIRED'));
    count_done:=count_done+1;
  END LOOP;
  RETURN count_done;
END $$;

CREATE FUNCTION fi.reconcile_attempt(a uuid,actual bigint,evidence_text text,allow_retry boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE att fi.task_attempts; s fi.task_states; x fi.experiments; r fi.budget_reservations; ns text; BEGIN
  SELECT * INTO STRICT att FROM fi.task_attempts WHERE id=a;
  s:=fi.ledger_lock(att.task_id);
  SELECT e.* INTO x FROM fi.expected_tasks q JOIN fi.experiments e ON e.id=q.experiment_id WHERE q.id=att.task_id;
  SELECT * INTO STRICT r FROM fi.budget_reservations WHERE attempt_id=a;
  IF actual IS NULL OR allow_retry IS NULL OR evidence_text IS NULL OR length(btrim(evidence_text)) NOT BETWEEN 1 AND 2000 THEN
    RAISE EXCEPTION 'reconciliation needs known cost and evidence' USING ERRCODE='22023'; END IF;
  IF r.state='SETTLED' THEN
    IF r.actual_microusd=actual AND r.evidence=evidence_text THEN RETURN; END IF;
    RAISE EXCEPTION 'conflicting reconciliation' USING ERRCODE='55000';
  END IF;
  IF r.state<>'HELD_UNKNOWN' THEN RAISE EXCEPTION 'attempt is not awaiting reconciliation' USING ERRCODE='55000'; END IF;
  PERFORM fi.ledger_settle(a,actual,evidence_text);
  IF s.status='UNCERTAIN' AND s.active_attempt=a THEN
    ns:=CASE WHEN clock_timestamp()>=x.deadline THEN 'TIMED_OUT'
      WHEN allow_retry AND s.attempt_no<(x.spec->>'max_attempts')::integer THEN 'QUEUED' ELSE 'FAILED' END;
    UPDATE fi.task_states SET status=ns,completed_at=CASE WHEN ns='QUEUED' THEN NULL ELSE clock_timestamp() END WHERE task_id=att.task_id;
  END IF;
  PERFORM fi.ledger_emit(x.id,att.task_id,'COST_RECONCILED',jsonb_build_object('attempt_id',a,'actual_microusd',actual,'retry_authorized',allow_retry));
END $$;

CREATE FUNCTION fi.cancel_task(t uuid,reason text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE s fi.task_states; att fi.task_attempts; e uuid; BEGIN
  IF reason IS NULL OR length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'reason required' USING ERRCODE='22023'; END IF;
  s:=fi.ledger_lock(t);
  IF s.status='CANCELLED' THEN RETURN; END IF;
  IF s.status NOT IN ('QUEUED','RUNNING','UNCERTAIN') THEN RAISE EXCEPTION 'cannot cancel terminal task' USING ERRCODE='55000'; END IF;
  SELECT experiment_id INTO e FROM fi.expected_tasks WHERE id=t;
  IF s.status='RUNNING' THEN
    SELECT * INTO att FROM fi.task_attempts WHERE id=s.active_attempt;
    PERFORM fi.ledger_settle(att.id,CASE WHEN att.call_started_at IS NULL THEN 0 ELSE NULL END,'cancelled lease');
    UPDATE fi.task_attempts SET state=CASE WHEN call_started_at IS NULL THEN 'CANCELLED' ELSE 'UNCERTAIN' END,
      finished_at=clock_timestamp() WHERE id=att.id;
  END IF;
  UPDATE fi.task_states SET status='CANCELLED',lease_until=NULL,last_error='OPERATOR_CANCELLED',completed_at=clock_timestamp() WHERE task_id=t;
  PERFORM fi.ledger_emit(e,t,'TASK_CANCELLED',jsonb_build_object('reason',reason));
END $$;

CREATE FUNCTION fi.claim_outbox(lease_seconds integer DEFAULT 30) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE d fi.outbox_delivery; ev fi.outbox_events; BEGIN
  PERFORM fi.ledger_rc();
  IF lease_seconds IS NULL OR lease_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid lease' USING ERRCODE='22023'; END IF;
  SELECT * INTO d FROM fi.outbox_delivery WHERE acknowledged_at IS NULL
    AND (lease_until IS NULL OR lease_until<=clock_timestamp()) ORDER BY event_id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE fi.outbox_delivery SET owner=session_user,fence=fence+1,
    lease_until=clock_timestamp()+make_interval(secs=>lease_seconds) WHERE event_id=d.event_id;
  SELECT * INTO ev FROM fi.outbox_events WHERE id=d.event_id;
  RETURN jsonb_build_object('event_id',ev.id,'fence',d.fence+1,'kind',ev.kind,
    'experiment_id',ev.experiment_id,'task_id',ev.task_id,'detail',ev.detail);
END $$;
CREATE FUNCTION fi.ack_outbox(e bigint,f bigint) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE d fi.outbox_delivery; BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT d FROM fi.outbox_delivery WHERE event_id=e FOR UPDATE;
  IF f IS NULL OR d.fence<>f OR d.owner IS DISTINCT FROM session_user THEN
    RAISE EXCEPTION 'stale or foreign outbox token' USING ERRCODE='55000'; END IF;
  IF d.acknowledged_at IS NOT NULL THEN RETURN; END IF;
  IF clock_timestamp()>=d.lease_until THEN RAISE EXCEPTION 'expired outbox lease' USING ERRCODE='55000'; END IF;
  UPDATE fi.outbox_delivery SET acknowledged_at=clock_timestamp() WHERE event_id=e;
END $$;

CREATE VIEW fi.task_catalog AS SELECT q.*,s.status,s.attempt_no,s.fence,s.lease_until,s.last_error,s.completed_at
  FROM fi.expected_tasks q JOIN fi.task_states s ON s.task_id=q.id;
CREATE VIEW fi.experiment_summary AS
SELECT e.id,e.external_key,e.spec,e.manifest_sha256,e.deadline,b.spent_microusd,b.reserved_microusd,
  (e.spec->>'budget_microusd')::bigint-b.spent_microusd-b.reserved_microusd AS available_microusd,
  (SELECT count(*) FROM fi.expected_tasks t WHERE t.experiment_id=e.id) AS total_tasks,
  (SELECT count(*) FROM fi.task_catalog t WHERE t.experiment_id=e.id AND t.status='SUCCEEDED') AS succeeded,
  (SELECT count(*) FROM fi.task_catalog t WHERE t.experiment_id=e.id AND t.status IN ('FAILED','TIMED_OUT','CANCELLED')) AS failed,
  (SELECT count(*) FROM fi.task_catalog t WHERE t.experiment_id=e.id AND t.status='UNCERTAIN') AS uncertain
FROM fi.experiments e JOIN fi.budget_accounts b ON b.experiment_id=e.id;

REVOKE ALL ON ALL TABLES IN SCHEMA fi FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fi FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fi FROM PUBLIC;
GRANT USAGE ON SCHEMA fi TO fi_operator,fi_worker,fi_dispatcher;
GRANT SELECT ON fi.experiment_summary,fi.task_catalog TO fi_operator,fi_reader;
GRANT SELECT ON fi.experiments,fi.experiment_entries,fi.task_attempts,fi.budget_reservations,
  fi.task_submissions,fi.outbox_events TO fi_operator;
GRANT EXECUTE ON FUNCTION fi.register_experiment(text,uuid[],jsonb,jsonb),fi.bind_worker(uuid,text,name),
  fi.recover_tasks(uuid,integer),fi.reconcile_attempt(uuid,bigint,text,boolean),fi.cancel_task(uuid,text) TO fi_operator;
GRANT EXECUTE ON FUNCTION fi.claim_task(uuid,text),fi.heartbeat_task(uuid,uuid,bigint),
  fi.start_task_call(uuid,uuid,bigint),fi.finish_task(uuid,uuid,bigint,jsonb,text,bigint,text,boolean) TO fi_worker;
GRANT EXECUTE ON FUNCTION fi.claim_outbox(integer),fi.ack_outbox(bigint,bigint) TO fi_dispatcher;
-- No grants to fi_resolver. Workers cannot read peer results or ledger tables.
