-- Forward-only hardening: preserve 0001 and its applied checksum.
-- Permission admission is defined at one instant AFTER all source locks.
CREATE OR REPLACE FUNCTION fi.assert_sources(p_version uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, fi, pg_temp AS $$
DECLARE binding record; permission fi.source_permissions; moment timestamptz;
BEGIN
  -- A caller-controlled old REPEATABLE READ snapshot could otherwise miss an
  -- already committed revocation while locking an unchanged sources row.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'permission admission requires READ COMMITTED isolation' USING ERRCODE='25000';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM fi.question_sources WHERE version_id=p_version) THEN
    RAISE EXCEPTION 'question has no licensed source' USING ERRCODE='42501'; END IF;
  FOR binding IN SELECT * FROM fi.question_sources WHERE version_id=p_version ORDER BY source_id LOOP
    PERFORM 1 FROM fi.sources WHERE id=binding.source_id FOR SHARE;
  END LOOP;
  moment := clock_timestamp();
  FOR binding IN SELECT * FROM fi.question_sources WHERE version_id=p_version ORDER BY source_id LOOP
    SELECT * INTO STRICT permission FROM fi.source_permissions WHERE id=binding.permission_id;
    IF NOT (permission.allowed_uses @> ARRAY['internal_research','model_processing','model_evaluation','storage']::text[])
      OR moment < permission.valid_from OR moment >= permission.valid_until
      OR EXISTS (SELECT 1 FROM fi.permission_revocations WHERE permission_id=permission.id)
      OR EXISTS (SELECT 1 FROM fi.source_permissions WHERE source_id=permission.source_id AND revision>permission.revision) THEN
      RAISE EXCEPTION 'source permission unavailable, revoked, superseded or inadequate' USING ERRCODE='42501'; END IF;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION fi.assert_sources(uuid) FROM PUBLIC;
