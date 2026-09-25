import json
import os
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app import app


def test_webhook_rejects_missing_or_invalid_service_token(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "test-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.post("/webhooks/openobserve", json={"alert_name": "order-500"})
        assert response.status_code == 401
        response = client.post(
            "/webhooks/openobserve",
            headers={"X-Alert-Token": "wrong"},
            json={"alert_name": "order-500"},
        )
        assert response.status_code == 401


def test_webhook_rejects_malformed_payload_before_persistence(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "test-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/openobserve",
            headers={"X-Alert-Token": "test-token", "Content-Type": "application/json"},
            content=b"[]",
        )
        assert response.status_code == 400
        assert "Invalid alert payload" in response.text


def test_webhook_persists_and_deduplicates_on_local_postgres(monkeypatch):
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")

    from worker import start_outbox_dispatcher

    class NoopDispatcher:
        def stop(self):
            pass

    monkeypatch.setattr("worker.start_outbox_dispatcher", lambda: NoopDispatcher())
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "local-integration-token")
    trace_id = "d" * 32
    payload = {"alert_name": "integration-order-500", "trace_id": trace_id, "err_count": 1}
    with TestClient(app) as client:
        first = client.post("/webhooks/openobserve", headers={"X-Alert-Token": "local-integration-token"}, json=payload)
        repeated = client.post("/webhooks/openobserve", headers={"X-Alert-Token": "local-integration-token"}, json=payload)
    assert first.status_code == repeated.status_code == 202
    assert first.json()["duplicate"] is False
    assert repeated.json()["duplicate"] is True
    assert first.json()["incident_id"] == repeated.json()["incident_id"]
    assert first.json()["task_id"] == repeated.json()["task_id"]

    import psycopg

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM outbox_events WHERE payload->>'task_id' = %s", (first.json()["task_id"],))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (first.json()["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (first.json()["incident_id"],))
