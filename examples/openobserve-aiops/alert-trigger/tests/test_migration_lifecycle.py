from fastapi.testclient import TestClient

from app import app


def test_api_startup_does_not_apply_migrations(monkeypatch):
    class NoopDispatcher:
        def stop(self):
            pass

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("AIOPS_TEST_USERS_JSON", raising=False)
    monkeypatch.setattr("worker.start_outbox_dispatcher", lambda: NoopDispatcher())

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
