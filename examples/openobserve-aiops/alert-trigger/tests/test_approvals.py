import os
import uuid
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import app
from auth import hash_password
from migration_runner import apply_migrations
from models import IncidentInput
from store import create_incident, stable_fingerprint


class FakeOwner:
    def __init__(self, *, verify_mismatch=False):
        self.mode = "off"
        self.verify_mismatch = verify_mismatch
        self.actions = []
        self.reads_after_action = 0

    def state(self):
        if self.actions:
            self.reads_after_action += 1
            if self.verify_mismatch and self.reads_after_action == 1:
                return {"resource": "order-service", "chaos_mode": "off"}
        return {"resource": "order-service", "chaos_mode": self.mode}

    def set_chaos_mode(self, enabled, idempotency_key):
        self.actions.append((enabled, idempotency_key))
        self.mode = "on" if enabled else "off"
        return {"accepted": True, "action_id": idempotency_key, "chaos_mode": self.mode}


def test_approval_permissions_execution_verification_and_rollback(monkeypatch):
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
    monkeypatch.setenv("SESSION_SIGNING_KEY", "local-test-session-signing-key-123456")
    monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "true")
    apply_migrations(database_url)
    users = [
        (str(uuid.uuid4()), "approval-operator", "operator-passphrase-456", "operator", ["order-service"]),
        (str(uuid.uuid4()), "approval-reviewer", "reviewer-passphrase-456", "approver", ["order-service"]),
        (str(uuid.uuid4()), "approval-outsider", "outsider-passphrase-456", "viewer", ["billing"]),
    ]
    incidents = []
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            for user_id, username, password, role, scopes in users:
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, role, resource_scopes) VALUES (%s, %s, %s, %s, %s)",
                    (user_id, username, hash_password(password), role, scopes),
                )
        for name in ("approval-success", "approval-cancel", "approval-reject", "approval-rollback"):
            fingerprint = stable_fingerprint(IncidentInput("0" * 64, name))
            incidents.append((fingerprint, create_incident(conn, IncidentInput(fingerprint, name))))

    try:
        with TestClient(app) as client:
            def login(username, password):
                result = client.post("/auth/login", json={"username": username, "password": password})
                assert result.status_code == 200
                return {"Authorization": f"Bearer {result.json()['access_token']}"}

            operator = login("approval-operator", "operator-passphrase-456")
            reviewer = login("approval-reviewer", "reviewer-passphrase-456")
            outsider = login("approval-outsider", "outsider-passphrase-456")
            action = {"action": "set-chaos-mode", "resource": "order-service", "enabled": True}

            assert client.post(f"/api/incidents/{incidents[0][1]['incident_id']}/approvals", headers=outsider, json=action).status_code == 403
            assert client.post(f"/api/incidents/{incidents[0][1]['incident_id']}/approvals", headers=operator, json={**action, "shell": "echo unsafe"}).status_code == 422
            assert client.post(f"/api/approvals/{uuid.uuid4()}/decision", headers=operator, json={"decision": "approve"}).status_code == 403

            monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "false")
            disabled = client.post(f"/api/incidents/{incidents[0][1]['incident_id']}/approvals", headers=operator, json=action)
            assert disabled.status_code == 503
            monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "true")
            created = client.post(f"/api/incidents/{incidents[0][1]['incident_id']}/approvals", headers=operator, json=action)
            assert created.status_code == 201
            approval_id = created.json()["approval_id"]
            assert client.post(f"/api/approvals/{approval_id}/decision", headers=operator, json={"decision": "approve"}).status_code == 403
            assert client.post(f"/api/approvals/{approval_id}/execute", headers=operator).status_code == 409
            assert client.post(f"/api/approvals/{approval_id}/decision", headers=reviewer, json={"decision": "approve"}).json()["status"] == "approved"

            owner = FakeOwner()
            monkeypatch.setattr("app._order_action_client", lambda: owner)
            monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "false")
            assert client.post(f"/api/approvals/{approval_id}/execute", headers=operator).status_code == 503
            assert owner.actions == []
            monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "true")
            executed = client.post(f"/api/approvals/{approval_id}/execute", headers=operator)
            assert executed.status_code == 200
            assert executed.json()["verified"] is True
            assert owner.mode == "on"
            assert owner.actions == [(True, approval_id)]
            assert client.post(f"/api/approvals/{approval_id}/execute", headers=operator).status_code == 409

            cancel_incident = incidents[1][1]["incident_id"]
            cancel_id = client.post(f"/api/incidents/{cancel_incident}/approvals", headers=operator, json=action).json()["approval_id"]
            assert client.post(f"/api/approvals/{cancel_id}/cancel", headers=operator).json()["status"] == "cancelled"
            assert client.post(f"/api/approvals/{cancel_id}/decision", headers=reviewer, json={"decision": "approve"}).status_code == 409

            reject_incident = incidents[2][1]["incident_id"]
            reject_id = client.post(f"/api/incidents/{reject_incident}/approvals", headers=operator, json=action).json()["approval_id"]
            assert client.post(f"/api/approvals/{reject_id}/decision", headers=reviewer, json={"decision": "reject"}).json()["status"] == "rejected"
            assert client.post(f"/api/approvals/{reject_id}/execute", headers=operator).status_code == 409

            rollback_incident = incidents[3][1]["incident_id"]
            rollback_id = client.post(f"/api/incidents/{rollback_incident}/approvals", headers=operator, json=action).json()["approval_id"]
            assert client.post(f"/api/approvals/{rollback_id}/decision", headers=reviewer, json={"decision": "approve"}).status_code == 200
            rollback_owner = FakeOwner(verify_mismatch=True)
            monkeypatch.setattr("app._order_action_client", lambda: rollback_owner)
            failed = client.post(f"/api/approvals/{rollback_id}/execute", headers=operator)
            assert failed.status_code == 502
            assert "original state was restored" in failed.json()["detail"]
            assert rollback_owner.mode == "off"
            assert rollback_owner.actions == [(True, rollback_id), (False, f"{rollback_id}:rollback")]

            timeline = client.get(f"/api/incidents/{rollback_incident}", headers=operator).json()["timeline"]
            assert any(event["event_type"] == "action.rollback_completed" for event in timeline)
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                for fingerprint, incident in incidents:
                    incident_id = incident["incident_id"]
                    cursor.execute("DELETE FROM audit_events WHERE incident_id = %s", (incident_id,))
                    cursor.execute("DELETE FROM approvals WHERE incident_id = %s", (incident_id,))
                    cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
                    cursor.execute("DELETE FROM tasks WHERE incident_id = %s", (incident_id,))
                    cursor.execute("DELETE FROM incidents WHERE id = %s", (incident_id,))
                cursor.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", ([row[0] for row in users],))
