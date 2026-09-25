from fastapi.testclient import TestClient

from app import app
from migrate import main


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


def test_local_migration_command_can_reuse_local_database_url(monkeypatch):
    applied = []
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/aiops")
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setattr("migrate.apply_migrations", applied.append)

    assert main() == 0
    assert applied == ["postgresql://local/aiops"]
