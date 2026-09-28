import os
from urllib.parse import urlparse

import psycopg
import pytest


os.environ.setdefault("AIOPS_ENV", "local")
os.environ.setdefault("AIOPS_AUTH_MODE", "local")
os.environ.setdefault("SESSION_SIGNING_KEY", "test-only-session-signing-key-not-for-runtime")


@pytest.fixture(scope="session", autouse=True)
def bootstrap_local_test_database_roles():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        return
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")

    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_runtime') THEN
                    CREATE ROLE aiops_runtime LOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_migrator') THEN
                    CREATE ROLE aiops_migrator LOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_worker') THEN
                    CREATE ROLE aiops_worker LOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aiops_break_glass') THEN
                    CREATE ROLE aiops_break_glass NOLOGIN;
                END IF;
            END
            $$;
            """
        )
        conn.execute("ALTER ROLE aiops_break_glass NOLOGIN")
