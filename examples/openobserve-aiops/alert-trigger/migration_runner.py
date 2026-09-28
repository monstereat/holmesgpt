"""Apply the incident service's versioned database migrations."""

import os
from pathlib import Path

import psycopg

from db_config import connect_database, validate_database_url


MIGRATIONS_PATH = Path(__file__).parent / "migrations"
MIGRATION_LOCK_KEY = (0x484F4C4D, 0x41494F50)  # Namespaced as "HOLM" / "AIOP".


def apply_migrations(database_url: str) -> None:
    database_url = validate_database_url(database_url, "MIGRATION_DATABASE_URL", required=True)
    with connect_database(database_url) as conn:
        with conn.transaction():
            if os.getenv("AIOPS_ENV", "production") != "local":
                expected_role = os.getenv("MIGRATION_DATABASE_ROLE", "").strip()
                if not expected_role:
                    raise RuntimeError("MIGRATION_DATABASE_ROLE is required outside local mode")
                with conn.cursor() as cursor:
                    cursor.execute("SELECT current_user")
                    row = cursor.fetchone()
                if not row or row[0] != expected_role:
                    raise RuntimeError("MIGRATION_DATABASE_URL is not using the configured migration role")
            conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", MIGRATION_LOCK_KEY)
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            for path in sorted(MIGRATIONS_PATH.glob("*.sql")):
                version = path.stem
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
                    if cursor.fetchone():
                        continue
                script = path.read_text(encoding="utf-8").strip()
                if script.upper().startswith("BEGIN;"):
                    script = script[6:].lstrip()
                if script.upper().endswith("COMMIT;"):
                    script = script[:-7].rstrip()
                with conn.transaction():
                    conn.execute(script, prepare=False)
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING", (version,))
