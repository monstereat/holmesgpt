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


def test_workbench_rejects_missing_authentication(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/incidents").status_code == 401
        assert client.post("/api/tasks/not-a-task/retry").status_code == 401


def test_login_rbac_incident_timeline_retry_and_static_workbench(monkeypatch):
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
    apply_migrations(database_url)

    users = [
        (str(uuid.uuid4()), "demo-viewer", "viewer-passphrase-123", "viewer", ["order-service"]),
        (str(uuid.uuid4()), "demo-operator", "operator-passphrase-123", "operator", ["order-service"]),
        (str(uuid.uuid4()), "demo-approver", "approver-passphrase-123", "approver", ["order-service"]),
        (str(uuid.uuid4()), "demo-outsider", "outsider-passphrase-123", "viewer", ["billing"]),
        (str(uuid.uuid4()), "demo-admin", "admin-passphrase-123", "admin", ["order-service"]),
        (str(uuid.uuid4()), "demo-admin-2", "admin2-passphrase-123", "admin", ["order-service"]),
    ]
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            for user_id, username, password, role, scopes in users:
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, role, resource_scopes) VALUES (%s, %s, %s, %s, %s)",
                    (user_id, username, hash_password(password), role, scopes),
                )
        fingerprint = stable_fingerprint(IncidentInput("0" * 64, "workbench-order-500", ("9" * 32,), {"err_count": 1}))
        incident = create_incident(conn, IncidentInput(fingerprint, "workbench-order-500", ("9" * 32,), {"err_count": 1}))
        with conn.cursor() as cursor:
            cursor.execute("UPDATE tasks SET status = 'failed', attempt = 1, error_code = 'holmes_unavailable', completed_at = now() WHERE id = %s", (incident["task_id"],))

    try:
        with TestClient(app) as client:
            static_page = client.get("/")
            assert static_page.status_code == 200
            assert "事故工作台" in static_page.text
            static_script = client.get("/incidents.js")
            assert static_script.status_code == 200
            assert "/retrospective" in static_script.text
            assert "/api/users" in static_script.text
            assert "保存并标记已审核" in static_script.text

            def login(username, password):
                response = client.post("/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200
                return {"Authorization": f"Bearer {response.json()['access_token']}"}

            bad_login = client.post("/auth/login", json={"username": "demo-viewer", "password": "incorrect"})
            assert bad_login.status_code == 401

            viewer = login("demo-viewer", "viewer-passphrase-123")
            listing = client.get("/api/incidents", headers=viewer)
            assert listing.status_code == 200
            assert listing.json()["items"][0]["id"] == incident["incident_id"]
            details = client.get(f"/api/incidents/{incident['incident_id']}", headers=viewer)
            assert details.status_code == 200
            assert details.json()["tasks"][0]["error_code"] == "holmes_unavailable"
            assert client.post(f"/api/tasks/{incident['task_id']}/retry", headers=viewer).status_code == 403
            retrospective_url = f"/api/incidents/{incident['incident_id']}/retrospective"
            empty_retrospective = client.get(retrospective_url, headers=viewer)
            assert empty_retrospective.status_code == 200
            assert empty_retrospective.json()["status"] == "draft"
            assert client.put(retrospective_url, headers=viewer, json={"root_cause": "not allowed"}).status_code == 403

            outsider = login("demo-outsider", "outsider-passphrase-123")
            assert client.get("/api/incidents", headers=outsider).status_code == 403
            assert client.get("/api/users", headers=viewer).status_code == 403

            admin = login("demo-admin", "admin-passphrase-123")
            admin_users = client.get("/api/users", headers=admin)
            assert admin_users.status_code == 200
            assert {user["username"] for user in admin_users.json()["items"]} >= {
                "demo-viewer", "demo-admin", "demo-admin-2"
            }
            assert "password_hash" not in admin_users.text
            admin_user_ids = {user["username"]: user["id"] for user in admin_users.json()["items"]}
            assert client.post(f"/api/users/{uuid.uuid4()}/disable", headers=admin).status_code == 404
            assert client.post(f"/api/users/{admin_user_ids['demo-admin']}/disable", headers=admin).status_code == 409

            disabled_user = client.post(f"/api/users/{admin_user_ids['demo-outsider']}/disable", headers=admin)
            assert disabled_user.status_code == 200
            assert disabled_user.json()["status"] == "disabled"
            assert client.get("/auth/me", headers=outsider).status_code == 401
            assert client.get("/api/users", headers=admin).json()["items"]
            with psycopg.connect(database_url) as conn:
                event = conn.execute(
                    "SELECT event_type FROM audit_events WHERE event_type = 'user.disabled' AND details->>'user_id' = %s",
                    (admin_user_ids["demo-outsider"],),
                ).fetchone()
                assert event == ("user.disabled",)
            assert client.post(f"/api/users/{admin_user_ids['demo-outsider']}/disable", headers=admin).json()["status"] == "already_disabled"

            admin_2 = login("demo-admin-2", "admin2-passphrase-123")
            assert client.post(f"/api/users/{admin_user_ids['demo-admin-2']}/disable", headers=admin).status_code == 200
            assert client.get("/auth/me", headers=admin_2).status_code == 401

            operator = login("demo-operator", "operator-passphrase-123")
            approver = login("demo-approver", "approver-passphrase-123")
            assert client.post(f"/api/tasks/{incident['task_id']}/retry", headers=approver).status_code == 403
            retrospective_body = {
                "impact": "单个订单请求失败",
                "root_cause": "测试环境启用了 chaos mode",
                "resolution": "经独立审批关闭 chaos mode",
                "action_items": ["为演示故障步骤补充值班检查"],
                "reviewed": False,
            }
            assert client.put(retrospective_url, headers=operator, json=retrospective_body).status_code == 403
            assert client.put(retrospective_url, headers=approver, json={**retrospective_body, "unexpected": True}).status_code == 422
            saved_draft = client.put(retrospective_url, headers=approver, json=retrospective_body)
            assert saved_draft.status_code == 200
            assert saved_draft.json()["status"] == "draft"
            assert client.get(retrospective_url, headers=viewer).json()["action_items"] == retrospective_body["action_items"]
            reviewed = client.put(retrospective_url, headers=approver, json={**retrospective_body, "reviewed": True})
            assert reviewed.status_code == 200
            assert reviewed.json()["status"] == "reviewed"
            assert reviewed.json()["reviewed_by"] == "demo-approver"

            retry = client.post(f"/api/tasks/{incident['task_id']}/retry", headers=operator)
            assert retry.status_code == 202
            assert retry.json()["status"] == "queued"
            assert client.post(f"/api/tasks/{incident['task_id']}/retry", headers=operator).status_code == 409
            after_retry = client.get(f"/api/incidents/{incident['incident_id']}", headers=operator).json()
            assert after_retry["tasks"][0]["status"] == "queued"
            assert any(event["event_type"] == "task.manual_retry_requested" for event in after_retry["timeline"])
            assert {event["event_type"] for event in after_retry["timeline"]} >= {"retrospective.draft", "retrospective.reviewed"}

            logout = client.post("/auth/logout", headers=viewer)
            assert logout.status_code == 200
            assert client.get("/auth/me", headers=viewer).status_code == 401
            refreshed_viewer = login("demo-viewer", "viewer-passphrase-123")
            assert client.get("/auth/me", headers=refreshed_viewer).status_code == 200
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM audit_events WHERE incident_id = %s", (incident["incident_id"],))
                cursor.execute("DELETE FROM audit_events WHERE actor_id = ANY(%s::uuid[])", ([row[0] for row in users],))
                cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
                cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
                cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))
                cursor.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", ([row[0] for row in users],))
