"""Narrow evidence and live-call gateway; inherits short committed transactions."""
from __future__ import annotations
from uuid import UUID
from future_intelligence.tasks.repository import TaskRepository, uuid_text


class EvidenceRepository(TaskRepository):
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
