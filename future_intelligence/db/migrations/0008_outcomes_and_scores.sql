-- P0-05: question-version outcomes shared by every model; blind two-person approval.
CREATE TABLE fi.outcome_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), version_id uuid NOT NULL REFERENCES fi.question_versions,
  source_id uuid NOT NULL REFERENCES fi.sources, permission_id uuid NOT NULL REFERENCES fi.source_permissions,
  release_key text NOT NULL, published_at timestamptz NOT NULL, revision_order integer NOT NULL CHECK(revision_order>=0),
  value_text text NOT NULL, document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),
  source_reference text NOT NULL, observed_at timestamptz NOT NULL DEFAULT clock_timestamp(), recorded_by name NOT NULL DEFAULT session_user,
  UNIQUE(version_id,source_id,release_key), UNIQUE(version_id,source_id,published_at,revision_order)
);
CREATE TABLE fi.resolution_proposals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), version_id uuid NOT NULL REFERENCES fi.question_versions,
  parent_id uuid REFERENCES fi.resolution_proposals,
  kind text NOT NULL CHECK(kind IN ('RESOLVED','VOID','DISPUTED')),
  evidence_id uuid REFERENCES fi.outcome_evidence, reason text NOT NULL, fallback_reason text,
  proposed_by name NOT NULL DEFAULT session_user, proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK((kind='RESOLVED')=(evidence_id IS NOT NULL))
);
CREATE TABLE fi.resolution_heads (
  version_id uuid PRIMARY KEY REFERENCES fi.question_versions, latest_id uuid NOT NULL REFERENCES fi.resolution_proposals
);
CREATE TABLE fi.resolution_confirmations (
  proposal_id uuid PRIMARY KEY REFERENCES fi.resolution_proposals, confirmed_by name NOT NULL DEFAULT session_user,
  confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(), review_note text NOT NULL
);
CREATE TABLE fi.score_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), experiment_id uuid NOT NULL REFERENCES fi.experiments,
  algorithm text NOT NULL, context_text text NOT NULL, context_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(experiment_id,algorithm,context_sha256)
);
CREATE TABLE fi.score_rows (
  run_id uuid REFERENCES fi.score_runs, task_id uuid REFERENCES fi.expected_tasks,
  entry_key text NOT NULL, question_version_id uuid NOT NULL REFERENCES fi.question_versions,
  resolution_id uuid REFERENCES fi.resolution_proposals, event_cluster text NOT NULL, cohort_key text NOT NULL,
  metric text NOT NULL, state text NOT NULL, primary_score numeric, diagnostic_score numeric,
  baseline_score numeric, log_loss text,
  PRIMARY KEY(run_id,task_id)
);

CREATE FUNCTION fi.add_outcome_evidence(v uuid,p uuid,key text,published timestamptz,revision int,val text,sha text,ref text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE q fi.question_versions; perm fi.source_permissions; old fi.outcome_evidence; eid uuid; moment timestamptz;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT q FROM fi.question_versions WHERE id=v;
  SELECT * INTO STRICT perm FROM fi.source_permissions WHERE id=p;
  PERFORM 1 FROM fi.sources WHERE id=perm.source_id FOR SHARE;
  PERFORM 1 FROM fi.question_versions WHERE id=v FOR UPDATE;
  moment:=clock_timestamp();
  IF NOT EXISTS(SELECT 1 FROM fi.question_sources WHERE version_id=v AND source_id=perm.source_id)
    OR NOT(perm.allowed_uses @> ARRAY['internal_research','storage','model_evaluation']::text[])
    OR moment<perm.valid_from OR moment>=perm.valid_until
    OR EXISTS(SELECT 1 FROM fi.permission_revocations WHERE permission_id=p)
    OR EXISTS(SELECT 1 FROM fi.source_permissions WHERE source_id=perm.source_id AND revision>perm.revision) THEN
    RAISE EXCEPTION 'result source not licensed' USING ERRCODE='42501'; END IF;
  IF key IS NULL OR length(btrim(key)) NOT BETWEEN 1 AND 200 OR ref IS NULL OR length(btrim(ref)) NOT BETWEEN 1 AND 2000
    OR sha IS NULL OR sha!~'^[0-9a-f]{64}$' OR published IS NULL OR revision IS NULL OR revision<0
    OR val IS NULL OR length(val)>100 OR val!~'^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    OR (q.spec->>'question_type'='binary' AND val NOT IN ('0','1')) THEN
    RAISE EXCEPTION 'invalid result evidence' USING ERRCODE='22023'; END IF;
  IF moment<q.outcome_not_before OR published<q.outcome_not_before OR published>q.resolution_deadline OR published>moment THEN
    RAISE EXCEPTION 'result outside registered observation window' USING ERRCODE='55000'; END IF;
  SELECT * INTO old FROM fi.outcome_evidence WHERE version_id=v AND source_id=perm.source_id AND release_key=key;
  IF FOUND THEN
    IF old.value_text<>val OR old.document_sha256<>sha OR old.published_at<>published OR old.revision_order<>revision THEN
      RAISE EXCEPTION 'release revision conflict; register a distinct official revision' USING ERRCODE='22023'; END IF; RETURN old.id;
  END IF;
  INSERT INTO fi.outcome_evidence(version_id,source_id,permission_id,release_key,published_at,revision_order,value_text,document_sha256,source_reference)
    VALUES(v,perm.source_id,p,key,published,revision,val,sha,ref) RETURNING id INTO eid;
  PERFORM fi.audit(v,'OUTCOME_EVIDENCE_CAPTURED',jsonb_build_object('evidence_id',eid,'acquisition','OPERATOR_VERIFIED_RELEASE'));
  RETURN eid;
END $$;

CREATE FUNCTION fi.selected_release(v uuid,s uuid) RETURNS uuid LANGUAGE sql
SET search_path=pg_catalog,fi,pg_temp AS $$
  SELECT e.id FROM fi.outcome_evidence e JOIN fi.question_versions q ON q.id=e.version_id
    WHERE e.version_id=v AND e.source_id=s AND e.published_at<=q.resolution_deadline
    ORDER BY CASE WHEN q.spec->>'data_revision_policy'='FIRST_RELEASE' THEN e.published_at END ASC,
      CASE WHEN q.spec->>'data_revision_policy'='FIRST_RELEASE' THEN e.revision_order END ASC,
      CASE WHEN q.spec->>'data_revision_policy'='LATEST_AT_RESOLUTION_DEADLINE' THEN e.published_at END DESC,
      CASE WHEN q.spec->>'data_revision_policy'='LATEST_AT_RESOLUTION_DEADLINE' THEN e.revision_order END DESC LIMIT 1
$$;
CREATE FUNCTION fi.propose_resolution(v uuid,k text,evidence uuid,reason_text text,expected_parent uuid DEFAULT NULL,fallback text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE q fi.question_versions; head uuid; ev fi.outcome_evidence; rid uuid;
BEGIN
  PERFORM fi.ledger_rc();
  IF pg_has_role(session_user,'fi_worker','MEMBER') OR pg_has_role(session_user,'fi_operator','MEMBER') OR pg_has_role(session_user,'fi_dashboard','MEMBER') OR pg_has_role(session_user,'fi_scorekeeper','MEMBER') OR pg_has_role(session_user,'fi_verifier','MEMBER') THEN
    RAISE EXCEPTION 'resolution login must be blind to forecasts' USING ERRCODE='42501'; END IF;
  SELECT * INTO STRICT q FROM fi.question_versions WHERE id=v FOR UPDATE;
  SELECT latest_id INTO head FROM fi.resolution_heads WHERE version_id=v FOR UPDATE;
  IF head IS DISTINCT FROM expected_parent THEN RAISE EXCEPTION 'stale resolution parent' USING ERRCODE='40001'; END IF;
  IF k IS NULL OR k NOT IN ('RESOLVED','VOID','DISPUTED') OR reason_text IS NULL OR length(btrim(reason_text)) NOT BETWEEN 10 AND 5000
    OR (k='RESOLVED') IS DISTINCT FROM (evidence IS NOT NULL) THEN
    RAISE EXCEPTION 'explicit kind, evidence and audit reason required' USING ERRCODE='22023'; END IF;
  IF clock_timestamp()<q.outcome_not_before THEN RAISE EXCEPTION 'outcome not yet admissible' USING ERRCODE='55000'; END IF;
  IF k='RESOLVED' THEN
    SELECT * INTO STRICT ev FROM fi.outcome_evidence WHERE id=evidence AND version_id=v;
    IF evidence IS DISTINCT FROM fi.selected_release(v,ev.source_id) THEN
      RAISE EXCEPTION 'evidence violates frozen revision policy' USING ERRCODE='22023'; END IF;
    IF q.spec->>'data_revision_policy'='LATEST_AT_RESOLUTION_DEADLINE' AND clock_timestamp()<q.resolution_deadline THEN
      RAISE EXCEPTION 'latest-at-deadline cannot resolve early' USING ERRCODE='55000'; END IF;
    IF EXISTS(SELECT 1 FROM fi.question_sources WHERE version_id=v AND source_id=ev.source_id AND priority>1)
      AND (fallback IS NULL OR length(btrim(fallback))<10) THEN
      RAISE EXCEPTION 'fallback source requires rule-based justification' USING ERRCODE='22023'; END IF;
  END IF;
  INSERT INTO fi.resolution_proposals(version_id,parent_id,kind,evidence_id,reason,fallback_reason)
    VALUES(v,head,k,evidence,reason_text,fallback) RETURNING id INTO rid;
  INSERT INTO fi.resolution_heads VALUES(v,rid) ON CONFLICT(version_id) DO UPDATE SET latest_id=excluded.latest_id;
  PERFORM fi.audit(v,'RESOLUTION_PROPOSED',jsonb_build_object('proposal_id',rid,'kind',k)); RETURN rid;
END $$;
CREATE FUNCTION fi.confirm_resolution(r uuid,note text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$ DECLARE p fi.resolution_proposals; ev fi.outcome_evidence; BEGIN
  SELECT * INTO STRICT p FROM fi.resolution_proposals WHERE id=r;
  PERFORM 1 FROM fi.question_versions WHERE id=p.version_id FOR UPDATE;
  PERFORM 1 FROM fi.resolution_heads WHERE version_id=p.version_id AND latest_id=r FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'superseded proposal' USING ERRCODE='55000'; END IF;
  IF p.proposed_by=session_user OR pg_has_role(session_user,'fi_worker','MEMBER') OR pg_has_role(session_user,'fi_operator','MEMBER') OR pg_has_role(session_user,'fi_dashboard','MEMBER') OR pg_has_role(session_user,'fi_scorekeeper','MEMBER') OR pg_has_role(session_user,'fi_verifier','MEMBER') THEN
    RAISE EXCEPTION 'independent blind reviewer required' USING ERRCODE='42501'; END IF;
  IF note IS NULL OR length(btrim(note)) NOT BETWEEN 10 AND 5000 THEN RAISE EXCEPTION 'review evidence required' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM fi.resolution_confirmations WHERE proposal_id=r) THEN RETURN; END IF;
  IF p.kind='RESOLVED' THEN
    SELECT * INTO STRICT ev FROM fi.outcome_evidence WHERE id=p.evidence_id;
    IF ev.id IS DISTINCT FROM fi.selected_release(p.version_id,ev.source_id) THEN
      RAISE EXCEPTION 'release set changed since proposal' USING ERRCODE='40001'; END IF;
  END IF;
  INSERT INTO fi.resolution_confirmations(proposal_id,review_note) VALUES(r,note);
  PERFORM fi.audit(p.version_id,'RESOLUTION_CONFIRMED',jsonb_build_object('proposal_id',r,'kind',p.kind));
END $$;
CREATE VIEW fi.outcome_catalog AS
  SELECT q.id AS version_id,p.id AS resolution_id,
    CASE WHEN c.proposal_id IS NULL THEN 'PENDING' ELSE p.kind END AS status,
    CASE WHEN c.proposal_id IS NOT NULL AND p.kind='RESOLVED' THEN e.value_text END AS value_text,
    p.reason,p.fallback_reason,p.proposed_by,c.confirmed_by,c.confirmed_at,e.source_reference,e.document_sha256,
    e.published_at,e.observed_at,q.spec,q.event_cluster
  FROM fi.question_versions q LEFT JOIN fi.resolution_heads h ON h.version_id=q.id
    LEFT JOIN fi.resolution_proposals p ON p.id=h.latest_id
    LEFT JOIN fi.resolution_confirmations c ON c.proposal_id=p.id LEFT JOIN fi.outcome_evidence e ON e.id=p.evidence_id;

CREATE FUNCTION fi.score_experiment(e uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,fi,pg_temp AS $$
DECLARE x fi.experiments; b fi.batch_catalog; reg fi.batch_catalog; ctx text; hash text; run uuid; row record;
  task jsonb; sub jsonb; probs jsonb; o fi.outcome_catalog; first_claim timestamptz;
  ok_time boolean; ps numeric; base numeric; loss text; bins int; y int; i int; cdf numeric; target numeric; uniform_cdf numeric;
  primary_val numeric; state_text text; metric_text text; cohort text;
BEGIN
  PERFORM fi.ledger_rc();
  SELECT * INTO STRICT x FROM fi.experiments WHERE id=e;
  PERFORM 1 FROM fi.budget_accounts WHERE experiment_id=e FOR UPDATE;
  -- Outcome writers lock the question row too; stable order avoids cross-experiment cycles.
  FOR row IN SELECT DISTINCT question_version_id FROM fi.expected_tasks WHERE experiment_id=e ORDER BY question_version_id LOOP
    PERFORM 1 FROM fi.question_versions WHERE id=row.question_version_id FOR SHARE;
  END LOOP;
  SELECT * INTO STRICT b FROM fi.batch_catalog WHERE experiment_id=e AND kind='FORECAST';
  SELECT * INTO STRICT reg FROM fi.batch_catalog WHERE experiment_id=e AND kind='REGISTRATION';
  IF b.subject_sha256 IS NULL OR reg.subject_sha256 IS NULL THEN RAISE EXCEPTION 'both complete batches must be sealed' USING ERRCODE='55000'; END IF;
  SELECT min(a.claimed_at) INTO first_claim FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=e;
  ok_time:=coalesce(x.spec->>'run_kind'='LIVE' AND NOT(reg.trust_profile->>'fixture')::bool
    AND reg.verified_upper_time<=first_claim AND b.verified_upper_time<=x.deadline,false);
  SELECT jsonb_build_object('algorithm','fi.score.v1','experiment_id',e,'run_kind',x.spec->>'run_kind',
    'registration_subject',reg.subject_sha256,'forecast_subject',b.subject_sha256,'eligible_time',ok_time,
    'registration_upper_time',reg.verified_upper_time,'forecast_upper_time',b.verified_upper_time,'first_claim',first_claim,
    'outcomes',jsonb_agg(to_jsonb(o) ORDER BY o.version_id))::text INTO ctx FROM fi.outcome_catalog o
    WHERE o.version_id IN(SELECT question_version_id FROM fi.expected_tasks WHERE experiment_id=e);
  hash:=encode(sha256(convert_to(ctx,'UTF8')),'hex');
  SELECT id INTO run FROM fi.score_runs WHERE experiment_id=e AND algorithm='fi.score.v1' AND context_sha256=hash;
  IF FOUND THEN RETURN run; END IF;
  INSERT INTO fi.score_runs(experiment_id,algorithm,context_text,context_sha256) VALUES(e,'fi.score.v1',ctx,hash) RETURNING id INTO run;
  FOR row IN SELECT record_text FROM fi.batch_records WHERE batch_id=b.id AND ordinal>0 ORDER BY ordinal LOOP
    task:=row.record_text::jsonb;sub:=task->'submission';
    SELECT * INTO STRICT o FROM fi.outcome_catalog WHERE version_id=(task->>'question_version_id')::uuid;
    metric_text:=CASE WHEN o.spec->>'question_type'='binary' THEN 'brier' ELSE 'normalized_rps' END;
    cohort:=encode(sha256(convert_to(jsonb_build_object('suite',x.spec->>'suite','run_kind',x.spec->>'run_kind',
      'domain',o.spec->>'domain','track',o.spec->>'track','question_type',o.spec->>'question_type','bins',o.spec->'bin_edges','unit',o.spec->>'unit')::text,'UTF8')),'hex');
    ps:=NULL;base:=NULL;loss:=NULL;primary_val:=NULL;state_text:=o.status;
    IF o.status='RESOLVED' THEN
      bins:=CASE WHEN metric_text='brier' THEN 2 ELSE jsonb_array_length(o.spec->'bin_edges')+1 END;
      IF bins=2 AND metric_text='brier' THEN y:=o.value_text::integer;
      ELSE SELECT count(*) INTO y FROM jsonb_array_elements_text(o.spec->'bin_edges') z WHERE o.value_text::numeric>=z::numeric; END IF;
      base:=0;
      FOR i IN 0..bins-2 LOOP
        uniform_cdf:=((1000000/bins)*(i+1)+least(i+1,1000000%bins))::numeric/1000000;
        target:=CASE WHEN y<=i THEN 1 ELSE 0 END;
        base:=base+power(uniform_cdf-target,2)/(bins-1);
      END LOOP;
      IF sub IS NULL OR sub='null'::jsonb THEN state_text:='MISSING_PREDICTION';
      ELSE
        probs:=sub->'probabilities_ppm';cdf:=0;ps:=0;
        FOR i IN 0..bins-2 LOOP
          cdf:=cdf+(probs->>i)::numeric/1000000;target:=CASE WHEN y<=i THEN 1 ELSE 0 END;
          ps:=ps+power(cdf-target,2)/(bins-1);
        END LOOP;
        IF metric_text='brier' THEN
          loss:=CASE WHEN (probs->>y)::numeric=0 THEN 'Infinity' ELSE (-ln((probs->>y)::numeric/1000000))::text END;
        END IF;
        IF ok_time THEN primary_val:=ps;state_text:='TIMED_LIVE';
        ELSIF x.spec->>'run_kind'='FIXTURE' THEN state_text:='FIXTURE_DIAGNOSTIC';
        ELSE state_text:='UNVERIFIED_TIME'; END IF;
      END IF;
    END IF;
    INSERT INTO fi.score_rows VALUES(run,(task->>'task_id')::uuid,task->>'entry_key',o.version_id,o.resolution_id,
      o.event_cluster,cohort,metric_text,state_text,primary_val,ps,base,loss);
  END LOOP;
  PERFORM fi.audit(e,'SCORE_RUN_CREATED',jsonb_build_object('run_id',run,'context_sha256',hash));RETURN run;
END $$;
CREATE VIEW fi.score_summary AS
  SELECT s.run_id,s.entry_key,s.cohort_key,s.metric,count(*) AS registered_tasks,
    count(s.diagnostic_score) AS submitted_resolved,count(s.primary_score) AS timed_live_scored,
    count(*) FILTER(WHERE s.state='MISSING_PREDICTION') AS missing_predictions,
    count(*) FILTER(WHERE s.state IN('VOID','DISPUTED','PENDING')) AS unresolved_or_void,
    avg(s.primary_score) AS mean_primary,avg(s.diagnostic_score) AS conditional_mean_diagnostic,
    avg(s.baseline_score) AS mean_baseline_resolved,count(DISTINCT s.event_cluster) AS event_clusters
  FROM fi.score_rows s GROUP BY s.run_id,s.entry_key,s.cohort_key,s.metric;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['outcome_evidence','resolution_proposals','resolution_confirmations','score_runs','score_rows'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON fi.%I FOR EACH ROW EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON fi.%I FOR EACH STATEMENT EXECUTE FUNCTION fi.reject_mutation()',t);
    EXECUTE format('REVOKE ALL ON fi.%I FROM PUBLIC',t);
  END LOOP;
END $$;
REVOKE ALL ON fi.resolution_heads FROM PUBLIC;
REVOKE ALL ON FUNCTION fi.add_outcome_evidence(uuid,uuid,text,timestamptz,int,text,text,text),fi.selected_release(uuid,uuid),
  fi.propose_resolution(uuid,text,uuid,text,uuid,text),fi.confirm_resolution(uuid,text),fi.score_experiment(uuid) FROM PUBLIC;
GRANT SELECT ON fi.question_catalog,fi.sources,fi.source_permissions,fi.question_sources TO fi_resolution_reviewer;
GRANT SELECT ON fi.outcome_evidence,fi.resolution_proposals,fi.resolution_heads,fi.resolution_confirmations,fi.outcome_catalog TO fi_resolver,fi_resolution_reviewer,fi_operator,fi_scorekeeper;
GRANT EXECUTE ON FUNCTION fi.add_outcome_evidence(uuid,uuid,text,timestamptz,int,text,text,text),fi.propose_resolution(uuid,text,uuid,text,uuid,text) TO fi_resolver;
GRANT EXECUTE ON FUNCTION fi.confirm_resolution(uuid,text) TO fi_resolution_reviewer;
GRANT EXECUTE ON FUNCTION fi.score_experiment(uuid) TO fi_scorekeeper;
GRANT SELECT ON fi.score_runs,fi.score_rows,fi.score_summary,fi.outcome_catalog,fi.batch_catalog,fi.experiment_summary TO fi_scorekeeper,fi_operator,fi_dashboard;
-- Dashboard is a trusted internal viewer, not a worker or blinded resolver.
