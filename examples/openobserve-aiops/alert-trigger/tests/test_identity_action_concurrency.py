import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import ACTION_OWNER_RESOURCE_LOCK, USER_LIFECYCLE_LOCK, app
from auth import hash_password
from migration_runner import apply_migrations
from models import IncidentInput
from store import create_incident, stable_fingerprint


def test_disabling_user_waits_for_inflight_approved_action(monkeypatch):
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")

    from worker import start_outbox_dispatcher

    class NoopDispatcher:
        def stop(self):
            pass

    class BlockingOwner:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.mode = "off"

        def state(self):
            return {"resource": "order-service", "chaos_mode": self.mode}

        def set_chaos_mode(self, enabled, _idempotency_key):
            self.started.set()
            if not self.release.wait(timeout=10):
                raise TimeoutError("test did not release the blocked owner action")
            self.mode = "on" if enabled else "off"

    monkeypatch.setattr("worker.start_outbox_dispatcher", lambda *_args: NoopDispatcher())
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SESSION_SIGNING_KEY", "local-test-session-signing-key-123456")
    monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "true")
    apply_migrations(database_url)

    operator_id = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    reviewer_id = str(uuid.uuid4())
    users = [
        (operator_id, "concurrency-operator", "operator-passphrase-456", "operator"),
        (admin_id, "concurrency-admin", "admin-passphrase-456", "admin"),
        (reviewer_id, "concurrency-reviewer", "reviewer-passphrase-456", "approver"),
    ]
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "identity-action-concurrency"))
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            for user_id, username, password, role in users:
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, role, resource_scopes) VALUES (%s, %s, %s, %s, %s)",
                    (user_id, username, hash_password(password), role, ["order-service"]),
                )
        incident = create_incident(conn, IncidentInput(fingerprint, "identity-action-concurrency"))

    owner = BlockingOwner()
    monkeypatch.setattr("app._order_action_client", lambda: owner)
    approval_id = None
    try:
        with TestClient(app) as operator_client, TestClient(app) as admin_client:
            def login(client, username, password):
                response = client.post("/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200
                return {"Authorization": f"Bearer {response.json()['access_token']}"}

            operator = login(operator_client, users[0][1], users[0][2])
            admin = login(admin_client, users[1][1], users[1][2])
            reviewer = login(admin_client, users[2][1], users[2][2])
            created = operator_client.post(
                f"/api/incidents/{incident['incident_id']}/approvals",
                headers=operator,
                json={"action": "set-chaos-mode", "resource": "order-service", "enabled": True},
            )
            assert created.status_code == 201
            approval_id = created.json()["approval_id"]
            assert admin_client.post(
                f"/api/approvals/{approval_id}/decision", headers=reviewer, json={"decision": "approve"}
            ).status_code == 200

            with ThreadPoolExecutor(max_workers=2) as executor:
                action_future = executor.submit(
                    operator_client.post, f"/api/approvals/{approval_id}/execute", headers=operator
                )
                assert owner.started.wait(timeout=5), "action owner was not called"

                with psycopg.connect(database_url) as conn:
                    assert conn.execute("SELECT pg_try_advisory_lock(%s)", (USER_LIFECYCLE_LOCK,)).fetchone() == (False,)
                    assert conn.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (ACTION_OWNER_RESOURCE_LOCK,)
                    ).fetchone() == (False,)

                disable_future = executor.submit(
                    admin_client.post, f"/api/users/{operator_id}/disable", headers=admin
                )
                assert not disable_future.done(), "account disable bypassed an in-flight owner action"
                owner.release.set()
                action_result = action_future.result(timeout=10)
                disable_result = disable_future.result(timeout=10)

            assert action_result.status_code == 200
            assert disable_result.status_code == 200
            assert admin_client.get("/auth/me", headers=operator).status_code == 401
    finally:
        owner.release.set()
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                user_ids = [row[0] for row in users]
                cursor.execute(
                    "DELETE FROM audit_events WHERE incident_id = %s OR actor_id = ANY(%s::uuid[])",
                    (incident["incident_id"], user_ids),
                )
                if approval_id:
                    cursor.execute("DELETE FROM action_executions WHERE approval_id = %s", (approval_id,))
                    cursor.execute("DELETE FROM approvals WHERE id = %s", (approval_id,))
                cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
                cursor.execute("DELETE FROM tasks WHERE incident_id = %s", (incident["incident_id"],))
                cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))
                cursor.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (user_ids,))
