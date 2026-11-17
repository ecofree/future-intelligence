-- Forward-only guards; signature validation remains the isolated verifier's job.
CREATE FUNCTION fi.verification_report_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x jsonb:=NEW.report; k text; g timestamptz; u timestamptz;
  fields text[]:=ARRAY['protocol','status','subject_sha256','gen_time','upper_time','accuracy_us',
    'policy_oid','ca_sha256','reply_sha256','fixture','verifier','revocation_status'];
BEGIN
  PERFORM fi.ledger_rc();
  IF pg_has_role(session_user,'fi_operator','MEMBER') OR pg_has_role(session_user,'fi_worker','MEMBER')
    OR pg_has_role(session_user,'fi_resolver','MEMBER') THEN
    RAISE EXCEPTION 'verifier requires isolated login' USING ERRCODE='42501'; END IF;
  IF x IS NULL OR jsonb_typeof(x)<>'object' OR NOT(x ?& fields)
    OR (SELECT count(*) FROM jsonb_object_keys(x))<>cardinality(fields) THEN
    RAISE EXCEPTION 'incomplete timestamp verification report' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY fields LOOP
    IF k IN('accuracy_us','fixture') THEN CONTINUE; END IF;
    IF jsonb_typeof(x->k) IS DISTINCT FROM 'string' OR length(btrim(x->>k)) NOT BETWEEN 1 AND 500 THEN
      RAISE EXCEPTION 'invalid verification field' USING ERRCODE='22023'; END IF;
  END LOOP;
  IF jsonb_typeof(x->'accuracy_us') IS DISTINCT FROM 'number' OR (x->>'accuracy_us')!~'^[0-9]{1,8}$'
    OR (x->>'accuracy_us')::bigint>60000000 OR jsonb_typeof(x->'fixture') IS DISTINCT FROM 'boolean'
    OR x->>'verifier' IS DISTINCT FROM 'openssl-ts+fi-tstinfo-v1'
    OR x->>'revocation_status' IS DISTINCT FROM 'NOT_CHECKED' THEN
    RAISE EXCEPTION 'unsupported verification report contract' USING ERRCODE='22023'; END IF;
  FOREACH k IN ARRAY ARRAY['gen_time','upper_time'] LOOP
    IF (x->>k)!~'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' THEN
      RAISE EXCEPTION 'verification time requires explicit offset' USING ERRCODE='22023'; END IF;
  END LOOP;
  g:=(x->>'gen_time')::timestamptz;u:=(x->>'upper_time')::timestamptz;
  IF u<>g+(x->>'accuracy_us')::bigint*interval '1 microsecond' OR NEW.upper_time<>u THEN
    RAISE EXCEPTION 'verification uncertainty must match upper time' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER verification_contract BEFORE INSERT ON fi.anchor_verifications
  FOR EACH ROW EXECUTE FUNCTION fi.verification_report_guard();

CREATE FUNCTION fi.delivery_plan_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$
BEGIN
  IF (SELECT count(*) FROM jsonb_array_elements(NEW.analysis_plan->'comparisons')) <>
     (SELECT count(DISTINCT jsonb_build_array(least(p->>0,p->>1),greatest(p->>0,p->>1)))
        FROM jsonb_array_elements(NEW.analysis_plan->'comparisons') p) THEN
    RAISE EXCEPTION 'duplicate or reversed comparison pair' USING ERRCODE='22023'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER unique_comparison_family BEFORE INSERT ON fi.commitment_batches
  FOR EACH ROW EXECUTE FUNCTION fi.delivery_plan_guard();
CREATE FUNCTION fi.resolution_isolation_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,fi,pg_temp AS $$ BEGIN
  PERFORM fi.ledger_rc();RETURN NEW;
END $$;
CREATE TRIGGER resolution_fresh_snapshot BEFORE INSERT ON fi.resolution_confirmations
  FOR EACH ROW EXECUTE FUNCTION fi.resolution_isolation_guard();

-- Newly discovered official vintages suspend current scoring until re-adjudicated.
-- Old confirmations and score runs remain immutable historical records.
CREATE OR REPLACE VIEW fi.outcome_catalog AS
  SELECT q.id AS version_id,p.id AS resolution_id,
    CASE WHEN c.proposal_id IS NULL THEN 'PENDING'
      WHEN p.kind='RESOLVED' AND p.evidence_id IS DISTINCT FROM chosen.id THEN 'STALE_EVIDENCE'
      ELSE p.kind END AS status,
    CASE WHEN c.proposal_id IS NOT NULL AND p.kind='RESOLVED' AND p.evidence_id=chosen.id THEN e.value_text END AS value_text,
    p.reason,p.fallback_reason,p.proposed_by,c.confirmed_by,c.confirmed_at,e.source_reference,e.document_sha256,
    e.published_at,e.observed_at,q.spec,q.event_cluster
  FROM fi.question_versions q LEFT JOIN fi.resolution_heads h ON h.version_id=q.id
    LEFT JOIN fi.resolution_proposals p ON p.id=h.latest_id
    LEFT JOIN fi.resolution_confirmations c ON c.proposal_id=p.id LEFT JOIN fi.outcome_evidence e ON e.id=p.evidence_id
    LEFT JOIN LATERAL (
      SELECT z.id FROM fi.outcome_evidence z WHERE z.version_id=q.id AND z.source_id=e.source_id AND z.published_at<=q.resolution_deadline
      ORDER BY CASE WHEN q.spec->>'data_revision_policy'='FIRST_RELEASE' THEN z.published_at END ASC,
        CASE WHEN q.spec->>'data_revision_policy'='FIRST_RELEASE' THEN z.revision_order END ASC,
        CASE WHEN q.spec->>'data_revision_policy'='LATEST_AT_RESOLUTION_DEADLINE' THEN z.published_at END DESC,
        CASE WHEN q.spec->>'data_revision_policy'='LATEST_AT_RESOLUTION_DEADLINE' THEN z.revision_order END DESC LIMIT 1
    ) chosen ON true;
CREATE OR REPLACE VIEW fi.score_summary AS
  SELECT s.run_id,s.entry_key,s.cohort_key,s.metric,count(*) AS registered_tasks,
    count(s.diagnostic_score) AS submitted_resolved,count(s.primary_score) AS timed_live_scored,
    count(*) FILTER(WHERE s.state='MISSING_PREDICTION') AS missing_predictions,
    count(*) FILTER(WHERE s.state IN('VOID','DISPUTED','PENDING','STALE_EVIDENCE')) AS unresolved_or_void,
    avg(s.primary_score) AS mean_primary,avg(s.diagnostic_score) AS conditional_mean_diagnostic,
    avg(s.baseline_score) AS mean_baseline_resolved,count(DISTINCT s.event_cluster) AS event_clusters
  FROM fi.score_rows s GROUP BY s.run_id,s.entry_key,s.cohort_key,s.metric;
REVOKE ALL ON FUNCTION fi.verification_report_guard(),fi.delivery_plan_guard(),fi.resolution_isolation_guard() FROM PUBLIC;
