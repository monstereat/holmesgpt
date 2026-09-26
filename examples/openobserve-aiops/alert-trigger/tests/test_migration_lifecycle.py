from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from app import app
from migrate import main
from migration_runner import MIGRATION_LOCK_KEY, apply_migrations


def test_api_startup_does_not_apply_migrations(monkeypatch):
    class NoopDispatcher:
        def stop(self):
            pass

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("AIOPS_TEST_USERS_JSON", raising=False)
    monkeypatch.setattr("worker.start_outbox_dispatcher", lambda: NoopDispatcher())

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_migration_command_requires_a_separate_url_outside_local(monkeypatch, capsys):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime/aiops")
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)

    assert main() == 2
    assert "MIGRATION_DATABASE_URL is required" in capsys.readouterr().err


def test_migration_command_uses_migration_url(monkeypatch):
    applied = []
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime/aiops")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://migrator/aiops")
    monkeypatch.setattr("migrate.apply_migrations", applied.append)

    assert main() == 0
    assert applied == ["postgresql://migrator/aiops"]


def test_migration_runner_rejects_non_tls_database_url_before_connect(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    connected = []
    monkeypatch.setattr("migration_runner.psycopg.connect", connected.append)

    with pytest.raises(RuntimeError, match="sslmode=verify-full"):
        apply_migrations("postgresql://migrator:secret@db.internal/aiops")

    assert connected == []


def test_local_migration_command_can_reuse_local_database_url(monkeypatch):
    applied = []
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/aiops")
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setattr("migrate.apply_migrations", applied.append)

    assert main() == 0
    assert applied == ["postgresql://local/aiops"]


def test_migration_runner_uses_transaction_scoped_database_lock(monkeypatch, tmp_path):
    statements = []

    class Cursor:
        def execute(self, statement, params):
            statements.append((statement, params))

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class Connection:
        def execute(self, statement, params=None, **kwargs):
            statements.append((statement, params))

        def cursor(self):
            return Cursor()

        def transaction(self):
            return nullcontext()

        def commit(self):
            statements.append(("COMMIT", None))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    migration = tmp_path / "0001_test.sql"
    migration.write_text("CREATE TABLE migration_lock_test (id integer);", encoding="utf-8")
    monkeypatch.setattr("migration_runner.psycopg.connect", lambda _: Connection())
    monkeypatch.setattr("migration_runner.MIGRATIONS_PATH", tmp_path)

    apply_migrations("postgresql://test/aiops")

    sql = [statement for statement, _ in statements]
    assert sql[0] == "SELECT pg_advisory_xact_lock(%s, %s)"
    assert statements[0][1] == MIGRATION_LOCK_KEY
    assert sql.index("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())") < sql.index("CREATE TABLE migration_lock_test (id integer);")
