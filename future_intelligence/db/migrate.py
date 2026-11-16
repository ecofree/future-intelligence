"""Transactional, checksum-checked forward-only migrations. No destructive reset."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import os


def migrate(dsn: str) -> list[str]:
    import psycopg
    applied: list[str] = []
    # One transaction serializes concurrent migrators and rolls back all changes
    # on failure. This credential is NEVER an application credential.
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(6834201001)")
        connection.execute("CREATE SCHEMA IF NOT EXISTS fi_meta")
        connection.execute("REVOKE ALL ON SCHEMA fi_meta FROM PUBLIC")
        connection.execute("""CREATE TABLE IF NOT EXISTS fi_meta.schema_migrations (
            version text PRIMARY KEY, sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        root = files("future_intelligence.db").joinpath("migrations")
        migrations = sorted((p for p in root.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
        if not migrations:
            raise RuntimeError("no packaged migrations found")
        known = {row[0]: row[1] for row in connection.execute(
            "SELECT version, sha256 FROM fi_meta.schema_migrations").fetchall()}
        expected = {p.name for p in migrations}
        if set(known) - expected:
            raise RuntimeError("database contains migrations unknown to this code version")
        for path in migrations:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if path.name in known:
                if known[path.name] != digest:
                    raise RuntimeError(f"applied migration checksum changed: {path.name}")
                continue
            connection.execute(raw.decode("utf-8"), prepare=False)
            connection.execute("INSERT INTO fi_meta.schema_migrations(version, sha256) VALUES (%s,%s)",
                               (path.name, digest))
            applied.append(path.name)
    return applied


def main() -> None:
    dsn = os.environ.get("FI_MIGRATION_DATABASE_URL")
    if not dsn:
        raise SystemExit("FI_MIGRATION_DATABASE_URL required; migration uses a separate owner credential")
    print({"applied": migrate(dsn)})


if __name__ == "__main__":
    main()
