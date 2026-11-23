-- Freeze the site verifier separately from experiment authors. Bind both kinds
-- of commitments, including a registration that predates installation of the ledger.
CREATE TABLE fi.series_trust (
  singleton boolean PRIMARY KEY REFERENCES fi.series_budget CHECK (singleton),
  profile jsonb NOT NULL, source_review_sha256 text NOT NULL CHECK(source_review_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.series_trust FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation();
CREATE FUNCTION fi.validate_series_trust() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,fi,pg_temp AS $$
BEGIN
  PERFORM fi.validate_delivery_plan('{"protocol":"fi.analysis.v1","seed":1,"resamples":200,"alpha_ppm":50000,
    "min_clusters":2,"comparisons":[],"stopping_rule":"FIXED_REGISTERED_BATCH"}'::jsonb,NEW.profile);
  IF NEW.profile->'fixture' IS DISTINCT FROM 'false'::jsonb OR NOT (NEW.profile ? 'revocation_policy') THEN
    RAISE EXCEPTION 'series trust requires a production revocation profile' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER validate_site_trust BEFORE INSERT ON fi.series_trust FOR EACH ROW EXECUTE FUNCTION fi.validate_series_trust();
CREATE FUNCTION fi.enforce_series_trust() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE e uuid; site jsonb;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM fi.series_budget) THEN RETURN NEW; END IF;
  SELECT profile INTO site FROM fi.series_trust;
  IF TG_TABLE_NAME='commitment_batches' THEN
    IF EXISTS(SELECT 1 FROM fi.operation_policies WHERE experiment_id=NEW.experiment_id AND mode='FORMAL')
       AND NEW.trust_profile IS DISTINCT FROM site THEN
      RAISE EXCEPTION 'SERIES_TRUST_PROFILE_MISMATCH' USING ERRCODE='55000'; END IF;
  ELSIF TG_TABLE_NAME='series_cutover' THEN
    IF site IS NULL THEN RAISE EXCEPTION 'SERIES_TRUST_REQUIRED' USING ERRCODE='55000'; END IF;
  ELSE
    SELECT experiment_id INTO STRICT e FROM fi.expected_tasks WHERE id=NEW.task_id;
    IF site IS NULL OR NOT EXISTS(SELECT 1 FROM fi.batch_catalog b WHERE b.experiment_id=e AND b.kind='REGISTRATION'
        AND b.trust_profile=site AND b.verified_upper_time<=clock_timestamp()) THEN
      RAISE EXCEPTION 'SERIES_TRUSTED_REGISTRATION_REQUIRED' USING ERRCODE='55000'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER enforce_series_trust BEFORE INSERT ON fi.commitment_batches FOR EACH ROW EXECUTE FUNCTION fi.enforce_series_trust();
CREATE TRIGGER enforce_series_trust BEFORE INSERT ON fi.series_cutover FOR EACH ROW EXECUTE FUNCTION fi.enforce_series_trust();
CREATE TRIGGER enforce_series_trust BEFORE INSERT ON fi.series_attempts FOR EACH ROW EXECUTE FUNCTION fi.enforce_series_trust();
REVOKE ALL ON fi.series_trust FROM PUBLIC;
REVOKE ALL ON FUNCTION fi.enforce_series_trust(),fi.validate_series_trust() FROM PUBLIC;
