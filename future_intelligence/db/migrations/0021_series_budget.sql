-- A replacement site starts sealed. Historical charges stay in the source database;
-- the imported opening liabilities are evidence-backed conservative amounts.
CREATE TABLE fi.series_budget (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  snapshot_sha256 text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  snapshot_at timestamptz NOT NULL,
  opening_holds jsonb NOT NULL CHECK (jsonb_typeof(opening_holds)='object'),
  limit_microusd bigint NOT NULL CHECK (limit_microusd>0),
  max_calls integer NOT NULL CHECK (max_calls>0),
  per_call_microusd bigint NOT NULL CHECK (per_call_microusd BETWEEN 1 AND limit_microusd),
  system_id text NOT NULL, database_oid oid NOT NULL, data_path text NOT NULL, port integer NOT NULL
);
CREATE TABLE fi.series_cutover (
  singleton boolean PRIMARY KEY REFERENCES fi.series_budget CHECK (singleton),
  fence_sha256 text NOT NULL CHECK (fence_sha256 ~ '^[0-9a-f]{64}$'),
  billing_sha256 text NOT NULL CHECK (billing_sha256 ~ '^[0-9a-f]{64}$'),
  covered_until timestamptz NOT NULL, additional_held_microusd bigint NOT NULL CHECK (additional_held_microusd>=0),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.series_permits (
  task_id uuid PRIMARY KEY REFERENCES fi.expected_tasks,
  authorized_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE fi.series_attempts (
  attempt_id uuid PRIMARY KEY REFERENCES fi.task_attempts,
  task_id uuid NOT NULL UNIQUE REFERENCES fi.series_permits
);
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['series_budget','series_cutover','series_permits','series_attempts'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()',t);
  END LOOP;
END $$;

CREATE FUNCTION fi.series_location_ok(s fi.series_budget) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT s.system_id=(SELECT system_identifier::text FROM pg_control_system())
    AND s.database_oid=(SELECT oid FROM pg_database WHERE datname=current_database())
    AND s.port=current_setting('port')::integer AND s.data_path=current_setting('data_directory')
    AND NOT pg_is_in_recovery()
$$;
CREATE FUNCTION fi.open_series_budget(snapshot_hash text, as_of timestamptz, holds jsonb,
  ceiling bigint, calls integer, per_call bigint) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE prev fi.series_budget;
BEGIN
  PERFORM fi.ledger_rc();
  LOCK TABLE fi.budget_reservations IN SHARE ROW EXCLUSIVE MODE;
  LOCK TABLE fi.series_budget IN SHARE ROW EXCLUSIVE MODE;
  SELECT * INTO prev FROM fi.series_budget;
  IF FOUND THEN
    IF fi.series_location_ok(prev) AND
      ROW(prev.snapshot_sha256,prev.snapshot_at,prev.opening_holds,prev.limit_microusd,prev.max_calls,prev.per_call_microusd)
        IS NOT DISTINCT FROM ROW(snapshot_hash,as_of,holds,ceiling,calls,per_call) THEN RETURN; END IF;
    RAISE EXCEPTION 'SERIES_OPENING_CONFLICT' USING ERRCODE='55000';
  END IF;
  IF as_of>clock_timestamp() OR holds IS NULL OR jsonb_typeof(holds)<>'object' OR holds='{}'::jsonb
    OR EXISTS(SELECT 1 FROM jsonb_each(holds) h WHERE h.key!~'^[a-z0-9_]{1,64}$'
      OR jsonb_typeof(h.value)<>'number' OR h.value::text!~'^[0-9]{1,15}$')
    OR (SELECT sum(value::bigint) FROM jsonb_each_text(holds))>ceiling THEN
    RAISE EXCEPTION 'invalid opening liabilities' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id
      JOIN fi.experiments e ON e.id=t.experiment_id WHERE e.spec->>'run_kind'='LIVE') THEN
    RAISE EXCEPTION 'use a fresh ledger or reconcile existing live attempts first' USING ERRCODE='55000'; END IF;
  INSERT INTO fi.series_budget(snapshot_sha256,snapshot_at,opening_holds,limit_microusd,max_calls,per_call_microusd,
      system_id,database_oid,data_path,port)
    SELECT snapshot_hash,as_of,holds,ceiling,calls,per_call,c.system_identifier::text,d.oid,
      current_setting('data_directory'),current_setting('port')::integer
    FROM pg_control_system() c, pg_database d WHERE d.datname=current_database();
END $$;

-- The owner records reviewed upstream evidence after retiring alternative paid writers.
-- No worker/operator login has cutover or permit authority.
CREATE FUNCTION fi.check_series_cutover() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE s fi.series_budget;
BEGIN
  SELECT * INTO STRICT s FROM fi.series_budget FOR UPDATE;
  IF NOT fi.series_location_ok(s) OR NEW.covered_until<s.snapshot_at OR NEW.covered_until>clock_timestamp()
    OR NEW.fence_sha256=NEW.billing_sha256 THEN
    RAISE EXCEPTION 'invalid series cutover evidence' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER validate_cutover BEFORE INSERT ON fi.series_cutover FOR EACH ROW EXECUTE FUNCTION fi.check_series_cutover();

CREATE VIEW fi.series_budget_status AS
  SELECT s.snapshot_sha256,s.snapshot_at,s.limit_microusd,s.max_calls,s.per_call_microusd,s.opening_holds,
    o.amount AS opening_microusd,coalesce(c.additional_held_microusd,0) AS additional_held_microusd,
    n.attempts,n.amount AS new_exposure_microusd, (c.singleton IS TRUE) AS cutover_recorded,
    s.limit_microusd-o.amount-coalesce(c.additional_held_microusd,0)-n.amount AS balance_microusd
  FROM fi.series_budget s LEFT JOIN fi.series_cutover c USING(singleton)
  CROSS JOIN LATERAL (SELECT sum(value::bigint)::bigint AS amount FROM jsonb_each_text(s.opening_holds)) o
  CROSS JOIN (SELECT count(*) AS attempts,coalesce(sum(coalesce(b.actual_microusd,b.reserved_microusd)),0)::bigint AS amount
    FROM fi.series_attempts a JOIN fi.budget_reservations b USING(attempt_id)) n;

CREATE FUNCTION fi.guard_series_dispatch() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE s fi.series_budget; t uuid; live boolean; formal_terra boolean; status fi.series_budget_status;
BEGIN
  IF TG_OP='UPDATE' THEN
    -- Serialize factual reconciliation with new claims; never discard an actual overrun.
    IF EXISTS(SELECT 1 FROM fi.series_attempts a WHERE a.attempt_id=OLD.attempt_id) THEN
      PERFORM 1 FROM fi.series_budget FOR UPDATE;
    END IF;
    RETURN NEW;
  END IF;
  SELECT q.id,e.spec->>'run_kind'='LIVE',p.mode='FORMAL' AND fi.valid_terra_generation(entry.config)
    INTO STRICT t,live,formal_terra FROM fi.task_attempts a JOIN fi.expected_tasks q ON q.id=a.task_id
    JOIN fi.experiments e ON e.id=q.experiment_id JOIN fi.experiment_entries entry
    ON entry.experiment_id=q.experiment_id AND entry.entry_key=q.entry_key
    LEFT JOIN fi.operation_policies p ON p.experiment_id=q.experiment_id WHERE a.id=NEW.attempt_id;
  IF NOT live THEN RETURN NEW; END IF;
  PERFORM fi.ledger_rc();
  SELECT * INTO s FROM fi.series_budget FOR UPDATE;
  IF NOT FOUND THEN
    IF formal_terra THEN RAISE EXCEPTION 'SERIES_LEDGER_REQUIRED' USING ERRCODE='55000'; END IF;
    RETURN NEW;
  END IF;
  IF NOT fi.series_location_ok(s) THEN RAISE EXCEPTION 'SERIES_WRITER_LOCATION_MISMATCH' USING ERRCODE='55000'; END IF;
  SELECT * INTO STRICT status FROM fi.series_budget_status;
  IF NOT status.cutover_recorded THEN RAISE EXCEPTION 'SERIES_UPSTREAM_CUTOVER_REQUIRED' USING ERRCODE='55000'; END IF;
  IF NOT coalesce(formal_terra,false) OR NOT EXISTS(SELECT 1 FROM fi.series_permits WHERE task_id=t) THEN
    RAISE EXCEPTION 'SERIES_TASK_NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
  IF TG_TABLE_NAME='budget_reservations' THEN
    IF NEW.reserved_microusd>s.per_call_microusd OR NEW.reserved_microusd>status.balance_microusd
      OR status.attempts>=s.max_calls THEN RAISE EXCEPTION 'SERIES_BUDGET_EXHAUSTED' USING ERRCODE='55000'; END IF;
    INSERT INTO fi.series_attempts(attempt_id,task_id) VALUES(NEW.attempt_id,t);
  ELSIF NOT EXISTS(SELECT 1 FROM fi.series_attempts a JOIN fi.budget_reservations b USING(attempt_id)
       WHERE a.attempt_id=NEW.attempt_id AND a.task_id=t AND b.state='RESERVED') OR status.balance_microusd<0 THEN
    RAISE EXCEPTION 'SERIES_RESERVATION_REQUIRED' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER enforce_series_reservation BEFORE INSERT OR UPDATE ON fi.budget_reservations
  FOR EACH ROW EXECUTE FUNCTION fi.guard_series_dispatch();
CREATE TRIGGER enforce_series_send BEFORE INSERT ON fi.provider_requests
  FOR EACH ROW EXECUTE FUNCTION fi.guard_series_dispatch();
REVOKE ALL ON fi.series_budget,fi.series_cutover,fi.series_permits,fi.series_attempts,fi.series_budget_status FROM PUBLIC;
REVOKE ALL ON FUNCTION fi.series_location_ok(fi.series_budget),fi.open_series_budget(text,timestamptz,jsonb,bigint,integer,bigint),
  fi.check_series_cutover(),fi.guard_series_dispatch() FROM PUBLIC;
GRANT SELECT ON fi.series_budget_status TO fi_operator;
