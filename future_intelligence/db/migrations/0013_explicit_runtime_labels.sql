-- Browser acceptance exposed that the generic table omits nested spec objects.
-- Project qualification labels explicitly; do not weaken the fixture assertion.
-- Keep original columns and order for all existing consumers and grants.
CREATE OR REPLACE VIEW fi.experiment_summary AS
SELECT e.id,e.external_key,e.spec,e.manifest_sha256,e.deadline,b.spent_microusd,b.reserved_microusd,
  (e.spec->>'budget_microusd')::bigint-b.spent_microusd-b.reserved_microusd AS available_microusd,
  (SELECT count(*) FROM fi.expected_tasks t WHERE t.experiment_id=e.id) AS total_tasks,
  (SELECT count(*) FROM fi.task_catalog t WHERE t.experiment_id=e.id AND t.status='SUCCEEDED') AS succeeded,
  (SELECT count(*) FROM fi.task_catalog t WHERE t.experiment_id=e.id AND t.status IN ('FAILED','TIMED_OUT','CANCELLED')) AS failed,
  (SELECT count(*) FROM fi.task_catalog t WHERE t.experiment_id=e.id AND t.status='UNCERTAIN') AS uncertain,
  e.spec->>'run_kind' AS run_kind,
  e.spec->>'suite' AS suite,
  coalesce(p.mode,'UNCONFIGURED') AS operation_mode
FROM fi.experiments e JOIN fi.budget_accounts b ON b.experiment_id=e.id
LEFT JOIN fi.operation_policies p ON p.experiment_id=e.id;
