"""Short, committed transactions. Never hold a connection across provider I/O."""
from __future__ import annotations

from uuid import UUID

from .spec import validate_registration


def uuid_text(value: str | UUID) -> str:
    """Accept identifiers from JSON strings and psycopg UUID result objects."""
    if not isinstance(value, (str, UUID)):
        raise ValueError("identifier must be a UUID or UUID string")
    return str(value if isinstance(value, UUID) else UUID(value))


class TaskRepository:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def _call(self, statement: str, params: tuple = ()):
        import psycopg
        with psycopg.connect(self._dsn) as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SET LOCAL statement_timeout = '20s'")
            row = connection.execute(statement, params).fetchone()
            if row is None:
                raise LookupError("record not found")
            value = row[0]
        # Commit (including the call-start marker) completes before the caller acts.
        return value

    def register(self, key: str, versions: list[str], entries: list[dict], spec: dict) -> str:
        from psycopg.types.json import Jsonb
        versions, entries, spec = validate_registration(versions, entries, spec)
        return str(self._call("SELECT fi.register_experiment(%s,%s::uuid[],%s,%s)",
                              (key, versions, Jsonb(entries), Jsonb(spec))))

    def bind_worker(self, experiment: str | UUID, entry: str, login: str) -> None:
        self._call("SELECT fi.bind_worker(%s::uuid,%s,%s::name)", (uuid_text(experiment), entry, login))

    def claim(self, experiment: str | UUID, run_kind: str, *, task_id: str | UUID | None = None) -> dict | None:
        if task_id is not None:
            return self._call("SELECT fi.claim_task(%s::uuid,%s,%s::uuid)",
                              (uuid_text(experiment), run_kind, uuid_text(task_id)))
        return self._call("SELECT fi.claim_task(%s::uuid,%s)", (uuid_text(experiment), run_kind))

    @staticmethod
    def _identity(lease: dict) -> tuple:
        fence = lease["fence"]
        if type(fence) is not int or fence < 1:
            raise ValueError("invalid fencing token")
        return uuid_text(lease["task_id"]), uuid_text(lease["attempt_id"]), fence

    def heartbeat(self, lease: dict):
        return self._call("SELECT fi.heartbeat_task(%s::uuid,%s::uuid,%s)", self._identity(lease))

    def start_call(self, lease: dict) -> bool:
        """False means already marked: NEVER resend a provider request on that basis."""
        return self._call("SELECT fi.start_task_call(%s::uuid,%s::uuid,%s)", self._identity(lease))

    def succeed(self, lease: dict, probabilities: list[int], response_sha256: str,
                actual_microusd: int | None) -> str:
        from psycopg.types.json import Jsonb
        return self._call("SELECT fi.finish_task(%s::uuid,%s::uuid,%s,%s,%s,%s,NULL,false)",
                          self._identity(lease) + (Jsonb(probabilities), response_sha256, actual_microusd))

    def fail(self, lease: dict, error_code: str, actual_microusd: int | None,
             retryable: bool = False) -> str:
        if type(retryable) is not bool:
            raise ValueError("retryable must be boolean")
        return self._call("SELECT fi.finish_task(%s::uuid,%s::uuid,%s,NULL,NULL,%s,%s,%s)",
                          self._identity(lease) + (actual_microusd, error_code, retryable))

    def recover(self, experiment: str | UUID, limit: int = 100) -> int:
        return self._call("SELECT fi.recover_tasks(%s::uuid,%s)", (uuid_text(experiment), limit))

    def reconcile(self, attempt: str | UUID, actual_microusd: int, evidence: str,
                  allow_retry: bool = False) -> None:
        self._call("SELECT fi.reconcile_attempt(%s::uuid,%s,%s,%s)",
                   (uuid_text(attempt), actual_microusd, evidence, allow_retry))

    def cancel(self, task: str | UUID, reason: str) -> None:
        self._call("SELECT fi.cancel_task(%s::uuid,%s)", (uuid_text(task), reason))

    def summary(self, experiment: str | UUID) -> dict:
        return self._call("SELECT row_to_json(s) FROM fi.experiment_summary s WHERE id=%s::uuid",
                          (uuid_text(experiment),))

    def claim_event(self, lease_seconds: int = 30) -> dict | None:
        return self._call("SELECT fi.claim_outbox(%s)", (lease_seconds,))

    def ack_event(self, event: dict) -> None:
        self._call("SELECT fi.ack_outbox(%s,%s)", (event["event_id"], event["fence"]))
