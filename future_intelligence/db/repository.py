"""Thin synchronous SQL gateway. One connection/transaction per operation.

Authentication is a database login; the caller cannot supply an audit actor or
freeze time. Group-role separation is enforced inside PostgreSQL.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from future_intelligence.questions.spec import validate_spec


class QuestionRepository:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def _call(self, statement: str, params: tuple = ()) -> Any:
        import psycopg
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(statement, params).fetchone()
            if row is None:
                raise LookupError("record not found")
            return row[0]

    def create_version(self, key: str, spec: dict, permission_ids: list[str],
                       expected_parent: str | None = None) -> str:
        from psycopg.types.json import Jsonb
        return str(self._call("SELECT fi.create_version(%s,%s,%s::uuid[],%s::uuid)", (
            key, Jsonb(validate_spec(spec)), [str(UUID(x)) for x in permission_ids],
            str(UUID(expected_parent)) if expected_parent else None)))

    def review(self, version_id: str, reason: str) -> None:
        self._call("SELECT fi.review_version(%s::uuid,%s)", (str(UUID(version_id)), reason))

    def freeze(self, version_id: str) -> str:
        return self._call("SELECT fi.freeze_version(%s::uuid)", (str(UUID(version_id)),))

    def close(self, version_id: str) -> None:
        self._call("SELECT fi.close_version(%s::uuid)", (str(UUID(version_id)),))

    def get(self, version_id: str) -> dict:
        data = self._call("SELECT row_to_json(q) FROM fi.question_catalog q WHERE id=%s::uuid",
                          (str(UUID(version_id)),))
        return data
