"""Narrow evidence and live-call gateway; inherits short committed transactions."""
from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID
from future_intelligence.tasks.repository import TaskRepository, uuid_text


class EvidenceRepository(TaskRepository):
    def claim_for_dispatch(self, experiment, task_id, count_record, *, environ=None):
        """Roll back the tentative formal claim and reservation on a bad count."""
        from future_intelligence.providers.input_count import validate_count_record
        from future_intelligence.providers.live import Blocked, build_request, credentials_for, validate_entry
        with self._transaction() as connection:
            lease = connection.execute(*self._claim_query(experiment, 'LIVE', task_id)).fetchone()[0]
            if lease is None:
                return None
            entry = lease['entry']
            if (entry['provider'] != 'openai' or entry['model_id'] != 'gpt-6.1-terra'
                    or 'generation' not in entry['parameters']):
                return lease
            gate = connection.execute('SELECT fi.worker_delivery_gate(%s::uuid)', (experiment,)).fetchone()[0]
            if not gate['ready'] or gate['mode'] not in ('FORMAL', 'DIAGNOSTIC'):
                raise Blocked('OPERATION_NOT_ADMITTED')
            if gate['mode'] == 'FORMAL':
                if task_id is None or count_record is None:
                    raise Blocked('FORMAL_TERRA_REQUIRES_EXACT_TASK_AND_COUNT')
                validate_entry(entry, lease['cap_microusd'])
                credentials_for(entry, environ)
                context = connection.execute('SELECT fi.live_context(%s::uuid,%s::uuid,%s)',
                                             self._identity(lease)).fetchone()[0]
                body = build_request(lease['entry'], context)
                try:
                    validate_count_record(body.encode('utf-8'), count_record,
                                          lease['entry']['parameters']['input_token_ceiling'], datetime.now(timezone.utc))
                except ValueError:
                    raise Blocked('INPUT_COUNT_VALIDATION_FAILED') from None
                lease['formal_terra'] = True
            return lease

    def supports_observation_v2(self) -> bool:
        # Probe separately: even an unreachable CASE arm resolves function names
        # when parsed, so a single SELECT would fail on a pre-0019 database.
        if self._call("SELECT to_regprocedure('fi.provider_observation_v2_supported()') IS NOT NULL") is not True:
            return False
        return self._call("SELECT fi.provider_observation_v2_supported()") is True

    def seal(self, experiment: str | UUID, packets: list[dict]) -> str:
        from psycopg.types.json import Jsonb
        return self._call("SELECT fi.seal_evidence(%s::uuid,%s)", (uuid_text(experiment), Jsonb(packets)))

    def context(self, lease: dict) -> dict:
        return self._call("SELECT fi.live_context(%s::uuid,%s::uuid,%s)", self._identity(lease))

    def prepare(self, lease: dict, body: str) -> bool:
        return self._call("SELECT fi.prepare_live_call(%s::uuid,%s::uuid,%s,%s)", self._identity(lease)+(body,))

    def observe(self, lease: dict, observation: dict) -> str:
        from psycopg.types.json import Jsonb
        return self._call("SELECT fi.record_provider_observation(%s::uuid,%s::uuid,%s,%s)",
                          self._identity(lease)+(Jsonb(observation),))
