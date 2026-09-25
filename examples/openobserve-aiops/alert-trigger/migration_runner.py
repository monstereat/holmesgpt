"""Apply the incident service's versioned database migrations."""

from pathlib import Path

import psycopg


MIGRATIONS_PATH = Path(__file__).parent / "migrations"


def apply_migrations(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
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
