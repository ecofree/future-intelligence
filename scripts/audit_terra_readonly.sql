-- One-shot private audit of the two known experiments. No migrations, writes,
-- project function calls, credentials, pg_authid, or environment values.
-- Run against the actual diagnostic DB and reference DB using an existing
-- authorized audit connection. Do not publish the output: it contains research.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path = pg_catalog;
SET LOCAL timezone = 'UTC';
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';

SELECT current_database() AS database_name, current_user AS active_role,
       session_user AS login_role, current_setting('server_version_num') AS server_version_num,
       current_setting('transaction_read_only') AS transaction_read_only,
       clock_timestamp() AS database_reported_capture_time;
SELECT version, sha256, applied_at FROM fi_meta.schema_migrations ORDER BY version;
SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS arguments,
       p.prokind, pg_get_userbyid(p.proowner) AS owner_name, p.prosecdef,
       p.provolatile, p.proparallel, p.proleakproof, p.proconfig,
       p.proacl::text AS explicit_acl,
       COALESCE(p.proacl, acldefault('f',p.proowner))::text AS effective_acl,
       pg_get_functiondef(p.oid) AS definition,
       encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex') AS definition_sha256
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname IN ('fi','fi_meta') AND p.prokind<>'a'
ORDER BY n.nspname,p.proname,arguments;
SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner_name,c.reloptions,
       pg_get_viewdef(c.oid,false) AS definition,
       encode(sha256(convert_to(pg_get_viewdef(c.oid,false),'UTF8')),'hex') AS definition_sha256
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('fi','fi_meta') AND c.relkind IN ('v','m') ORDER BY n.nspname,c.relname;
SELECT n.nspname,c.relname,k.conname,k.contype,k.convalidated,k.condeferrable,k.condeferred,
       pg_get_constraintdef(k.oid,false) AS definition
FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('fi','fi_meta') ORDER BY n.nspname,c.relname,k.conname;
SELECT n.nspname,c.relname,t.tgname,t.tgisinternal,t.tgenabled,k.conname,
       t.tgfoid::regprocedure::text AS trigger_function,pg_get_triggerdef(t.oid,false) AS definition
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_constraint k ON k.oid=t.tgconstraint
WHERE n.nspname IN ('fi','fi_meta') ORDER BY n.nspname,c.relname,t.tgisinternal,t.tgname;
SELECT nspname,pg_get_userbyid(nspowner) AS owner_name,nspacl::text AS explicit_acl,
       COALESCE(nspacl,acldefault('n',nspowner))::text AS effective_acl
FROM pg_namespace WHERE nspname IN ('fi','fi_meta') ORDER BY nspname;
SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner_name,
       c.relrowsecurity,c.relforcerowsecurity,c.relacl::text AS explicit_acl,
       COALESCE(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char" END,
                                  c.relowner))::text AS effective_acl
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('fi','fi_meta') AND c.relkind IN ('r','p','v','m','S','f')
ORDER BY n.nspname,c.relname;
WITH RECURSIVE roots(oid) AS (
  SELECT oid FROM pg_roles WHERE left(rolname,3)='fi_' OR rolname IN (
    SELECT login FROM fi.worker_bindings WHERE experiment_id IN (
      '7f6a447a-9835-484c-be93-8308044ba483'::uuid,'e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid))
  UNION SELECT nspowner FROM pg_namespace WHERE nspname IN ('fi','fi_meta')
  UNION SELECT c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname IN ('fi','fi_meta')
  UNION SELECT p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname IN ('fi','fi_meta')
), related(oid) AS (
  SELECT oid FROM roots UNION
  SELECT CASE WHEN m.member=r.oid THEN m.roleid ELSE m.member END
  FROM related r JOIN pg_auth_members m ON m.member=r.oid OR m.roleid=r.oid
)
SELECT r.rolname,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,
       r.rolreplication,r.rolbypassrls,r.rolconnlimit,r.rolvaliduntil
FROM pg_roles r JOIN related x ON x.oid=r.oid ORDER BY r.rolname;
WITH RECURSIVE roots(oid) AS (
  SELECT oid FROM pg_roles WHERE left(rolname,3)='fi_' OR rolname IN (
    SELECT login FROM fi.worker_bindings WHERE experiment_id IN (
      '7f6a447a-9835-484c-be93-8308044ba483'::uuid,'e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid))
  UNION SELECT nspowner FROM pg_namespace WHERE nspname IN ('fi','fi_meta')
  UNION SELECT c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname IN ('fi','fi_meta')
  UNION SELECT p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname IN ('fi','fi_meta')
), related(oid) AS (
  SELECT oid FROM roots UNION
  SELECT CASE WHEN m.member=r.oid THEN m.roleid ELSE m.member END
  FROM related r JOIN pg_auth_members m ON m.member=r.oid OR m.roleid=r.oid
)
SELECT granted.rolname AS granted_role,member.rolname AS member_role,grantor.rolname AS grantor_role,
       m.admin_option,m.inherit_option,m.set_option
FROM pg_auth_members m JOIN pg_roles granted ON granted.oid=m.roleid
JOIN pg_roles member ON member.oid=m.member JOIN pg_roles grantor ON grantor.oid=m.grantor
WHERE m.roleid IN (SELECT oid FROM related) OR m.member IN (SELECT oid FROM related)
ORDER BY granted.rolname,member.rolname;

-- Existence is explicit: missing rows are not evidence of zero spend.
-- Correlated counts avoid duplicating reservations in a multi-table join.
WITH wanted(id) AS (VALUES ('7f6a447a-9835-484c-be93-8308044ba483'::uuid),
                          ('e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid))
SELECT w.id AS requested_experiment_id,e.id IS NOT NULL AS experiment_exists,
       e.external_key,e.created_at,e.spec->>'budget_microusd' AS budget_cap_microusd,
       e.spec->>'attempt_cap_microusd' AS attempt_cap_microusd,p.mode,
       b.experiment_id IS NOT NULL AS account_exists,b.reserved_microusd,b.spent_microusd,
       (SELECT count(*) FROM fi.expected_tasks t WHERE t.experiment_id=w.id) AS task_count,
       (SELECT count(*) FROM fi.task_attempts a JOIN fi.expected_tasks t ON t.id=a.task_id
         WHERE t.experiment_id=w.id) AS attempt_count,
       (SELECT count(*) FROM fi.provider_requests r JOIN fi.expected_tasks t ON t.id=r.task_id
         WHERE t.experiment_id=w.id) AS provider_request_count,
       (SELECT count(*) FROM fi.provider_observations o JOIN fi.task_attempts a ON a.id=o.attempt_id
         JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=w.id) AS observation_count,
       (SELECT count(*) FROM fi.budget_reservations r JOIN fi.task_attempts a ON a.id=r.attempt_id
         JOIN fi.expected_tasks t ON t.id=a.task_id WHERE t.experiment_id=w.id) AS reservation_count
FROM wanted w LEFT JOIN fi.experiments e ON e.id=w.id
LEFT JOIN fi.operation_policies p ON p.experiment_id=w.id
LEFT JOIN fi.budget_accounts b ON b.experiment_id=w.id ORDER BY w.id;
SELECT t.experiment_id,t.id AS task_id,t.question_version_id,s.status AS task_status,
       s.attempt_no,s.active_attempt,a.id AS attempt_id,a.ordinal,a.state AS attempt_state,
       a.claimed_at,a.call_started_at,a.finished_at,pr.recorded_at AS request_recorded_at,
       pr.request_sha256,po.recorded_at AS observation_recorded_at,po.observation_sha256,
       br.reserved_microusd,br.actual_microusd,br.state AS reservation_state
FROM fi.expected_tasks t JOIN fi.task_states s ON s.task_id=t.id
LEFT JOIN fi.task_attempts a ON a.task_id=t.id LEFT JOIN fi.provider_requests pr ON pr.attempt_id=a.id
LEFT JOIN fi.provider_observations po ON po.attempt_id=a.id
LEFT JOIN fi.budget_reservations br ON br.attempt_id=a.id
WHERE t.experiment_id IN ('7f6a447a-9835-484c-be93-8308044ba483'::uuid,
                         'e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid)
ORDER BY t.experiment_id,t.id,a.ordinal;

-- Original UTF-8 strings, not reconstructed/reformatted JSON.
SELECT e.id,e.external_key,e.spec,e.manifest_text,e.manifest_sha256,
       encode(sha256(convert_to(e.manifest_text,'UTF8')),'hex') AS recomputed_manifest_sha256,
       e.created_at,p.mode,p.created_at AS mode_recorded_at
FROM fi.experiments e LEFT JOIN fi.operation_policies p ON p.experiment_id=e.id
WHERE e.id='e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid;
SELECT t.id AS task_id,v.id AS question_version_id,v.snapshot_text,v.snapshot_sha256,
       encode(sha256(convert_to(v.snapshot_text,'UTF8')),'hex') AS recomputed_snapshot_sha256,
       s.status,s.reviewed_at,s.reviewed_by,s.frozen_at
FROM fi.expected_tasks t JOIN fi.question_versions v ON v.id=t.question_version_id
JOIN fi.question_states s ON s.version_id=v.id
WHERE t.experiment_id='e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid ORDER BY t.id;
SELECT experiment_id,request_sha256,manifest_text,manifest_sha256,
       encode(sha256(convert_to(manifest_text,'UTF8')),'hex') AS recomputed_manifest_sha256,
       observed_at,created_by
FROM fi.evidence_seals WHERE experiment_id='e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid;
SELECT experiment_id,question_version_id,packet_text,packet_sha256,
       encode(sha256(convert_to(packet_text,'UTF8')),'hex') AS recomputed_packet_sha256
FROM fi.evidence_packets WHERE experiment_id='e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid
ORDER BY question_version_id;

-- Export only the known sanitized v1 shape. Unexpected keys => metadata/hash
-- only, never blindly export extra fields or a provider's raw response body.
WITH observations AS (
 SELECT t.id AS task_id,a.id AS attempt_id,pr.request_sha256,
        o.observation,o.observation_sha256,o.recorded_at,
        ARRAY(SELECT key FROM jsonb_object_keys(CASE WHEN jsonb_typeof(o.observation)='object'
          THEN o.observation ELSE '{}'::jsonb END) AS key ORDER BY key) AS keys,
        ARRAY(SELECT key FROM jsonb_object_keys(CASE WHEN jsonb_typeof(o.observation->'usage')='object'
          THEN o.observation->'usage' ELSE '{}'::jsonb END) AS key ORDER BY key) AS usage_keys,
        CASE WHEN jsonb_typeof(o.observation->'output')='object' THEN
          ARRAY(SELECT key FROM jsonb_object_keys(o.observation->'output') AS key ORDER BY key)
        END AS output_keys
 FROM fi.provider_observations o JOIN fi.task_attempts a ON a.id=o.attempt_id
 JOIN fi.expected_tasks t ON t.id=a.task_id JOIN fi.provider_requests pr ON pr.attempt_id=a.id
 WHERE t.experiment_id='e1e67317-4416-445b-b5c5-2f0197bf251d'::uuid
)
SELECT task_id,attempt_id,request_sha256,observation->>'protocol' AS protocol,
       keys,usage_keys,output_keys,observation_sha256,recorded_at,
       CASE WHEN jsonb_typeof(observation)='object'
          AND observation->>'protocol'='fi.provider-observation.v1'
          AND keys=ARRAY['http_status','model_reported','output','protocol','request_id','response_sha256','status','usage']
          AND observation->>'status' IN ('OK','INVALID_OUTPUT','INCOMPLETE','REFUSED','HTTP_ERROR','TIMEOUT','TRANSPORT_ERROR','RESPONSE_TOO_LARGE')
          AND (observation->'http_status'='null'::jsonb OR (jsonb_typeof(observation->'http_status')='number'
               AND observation->>'http_status'~'^[1-5][0-9][0-9]$'))
          AND (observation->'response_sha256'='null'::jsonb OR (jsonb_typeof(observation->'response_sha256')='string'
               AND observation->>'response_sha256'~'^[0-9a-f]{64}$'))
          AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['model_reported','request_id']) AS k
               WHERE observation->k IS DISTINCT FROM 'null'::jsonb AND
                 (jsonb_typeof(observation->k) IS DISTINCT FROM 'string'
                  OR observation->>k!~'^[A-Za-z0-9_./:-]{1,200}$'))
          AND usage_keys=ARRAY['cached_input_tokens','input_tokens','output_tokens']
          AND NOT EXISTS(SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(observation->'usage')='object'
               THEN observation->'usage' ELSE '{}'::jsonb END) AS u
               WHERE u.value<>'null'::jsonb AND (jsonb_typeof(u.value)<>'number'
                 OR (u.value#>>'{}')!~'^[0-9]{1,10}$'))
          AND (observation->'output'='null'::jsonb OR (
               output_keys=ARRAY['evidence_ids','probabilities_ppm','rationale']
               AND jsonb_typeof(observation#>'{output,rationale}')='string'
               AND length(observation#>>'{output,rationale}') BETWEEN 1 AND 4000
               AND jsonb_typeof(observation#>'{output,probabilities_ppm}')='array'
               AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN
                    jsonb_typeof(observation#>'{output,probabilities_ppm}')='array'
                    THEN observation#>'{output,probabilities_ppm}' ELSE '[]'::jsonb END) AS x
                    WHERE jsonb_typeof(x)<>'number' OR (x#>>'{}')!~'^[0-9]{1,7}$')
               AND jsonb_typeof(observation#>'{output,evidence_ids}')='array'
               AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN
                    jsonb_typeof(observation#>'{output,evidence_ids}')='array'
                    THEN observation#>'{output,evidence_ids}' ELSE '[]'::jsonb END) AS x
                    WHERE jsonb_typeof(x)<>'string' OR (x#>>'{}')!~'^[A-Za-z0-9_.:-]{1,200}$')
          ))
        THEN observation::text END AS stored_sanitized_observation_text,
       encode(sha256(convert_to(observation::text,'UTF8')),'hex') AS recomputed_observation_sha256
FROM observations ORDER BY task_id,attempt_id;

ROLLBACK;
