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
from task_errors import PermanentTaskError, RetryableTaskError
from tasks import claim_task, complete_task, fail_task, retry_delay
from worker import _dispatch_once, investigate_task, run_investigation


def test_retry_delay_is_bounded_exponential():
    assert [retry_delay(n) for n in (1, 2, 3, 4, 5, 8)] == [5, 10, 20, 40, 80, 300]
    with pytest.raises(ValueError):
        retry_delay(0)


def test_worker_checks_openobserve_toolset_before_model_call(monkeypatch):
    class UnavailableToolsetClient:
        def check_openobserve_toolset(self):
            raise RetryableTaskError("holmes_toolset_unavailable")

        def investigate(self, _task):
            pytest.fail("worker called Holmes chat while OpenObserve tools were unavailable")

    monkeypatch.setattr("worker._holmes_client", lambda: UnavailableToolsetClient())
    with pytest.raises(RetryableTaskError, match="holmes_toolset_unavailable"):
        run_investigation(None)


def test_task_attempts_and_permanent_failure_on_local_postgres():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    apply_migrations(database_url)
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-task", ("e" * 32,)))
    with psycopg.connect(database_url) as conn:
        result = create_incident(conn, IncidentInput(fingerprint, "integration-task", ("e" * 32,)))
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE tasks SET available_at = now() - interval '1 second', max_attempts = 1, result = '{\"stale\":true}'::jsonb WHERE id = %s",
                (result["task_id"],),
            )
        claimed = claim_task(conn, result["task_id"])
        assert claimed is not None and claimed.attempt == 1
        with conn.cursor() as cursor:
            cursor.execute("SELECT result FROM tasks WHERE id = %s", (result["task_id"],))
            assert cursor.fetchone() == (None,)
        assert claim_task(conn, result["task_id"]) is None
        failure_result = {
            "evidence_status": "unavailable",
            "evidence": [{"tool_name": "openobserve_search_logs", "status": "error", "error_code": "openobserve_tool_error"}],
        }
        assert fail_task(
            conn,
            claimed,
            "upstream_unavailable",
            retryable=True,
            failure_result=failure_result,
        ) == "failed"
        with conn.cursor() as cursor:
            cursor.execute("SELECT status, attempt, error_code, result FROM tasks WHERE id = %s", (result["task_id"],))
            assert cursor.fetchone() == ("failed", 1, "upstream_unavailable", failure_result)
            cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (result["task_id"],))
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (result["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (result["incident_id"],))


def test_successful_retry_clears_previous_attempt_error_on_local_postgres():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    apply_migrations(database_url)
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-retry-clears-error"))
    with psycopg.connect(database_url) as conn:
        incident = create_incident(conn, IncidentInput(fingerprint, "integration-retry-clears-error"))
    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE tasks SET max_attempts = 2 WHERE id = %s", (incident["task_id"],))
            first_attempt = claim_task(conn, incident["task_id"])
            assert first_attempt is not None and first_attempt.attempt == 1
            assert fail_task(conn, first_attempt, "upstream_unavailable", retryable=True) == "retrying"
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE tasks SET available_at = now() - interval '1 second' WHERE id = %s",
                    (incident["task_id"],),
                )
            retry = claim_task(conn, incident["task_id"])
            assert retry is not None and retry.attempt == 2
            with conn.cursor() as cursor:
                cursor.execute("SELECT error_code FROM tasks WHERE id = %s", (incident["task_id"],))
                assert cursor.fetchone() == (None,)
            assert complete_task(conn, retry, {"analysis": "recovered"}) is True
            with conn.cursor() as cursor:
                cursor.execute("SELECT status, error_code FROM tasks WHERE id = %s", (incident["task_id"],))
                assert cursor.fetchone() == ("completed", None)
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (incident["task_id"],))
                cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
                cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
                cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))


def test_expired_worker_cannot_persist_late_outcome_on_local_postgres():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    apply_migrations(database_url)
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-late-worker", ("e" * 32,)))
    with psycopg.connect(database_url) as conn:
        incident = create_incident(conn, IncidentInput(fingerprint, "integration-late-worker", ("e" * 32,)))
        claimed = claim_task(conn, incident["task_id"])
        assert claimed is not None
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (incident["task_id"],),
            )
        assert complete_task(conn, claimed, {"answer": "late"}) is False
        assert fail_task(conn, claimed, "late_failure", retryable=True) == "stale"
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT status, result, error_code FROM tasks WHERE id = %s", (incident["task_id"],))
            assert cursor.fetchone() == ("running", None, None)
            cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))


def test_worker_persists_failure_evidence_on_permanent_error(monkeypatch):
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    apply_migrations(database_url)
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-failure-evidence", ("e" * 32,)))
    with psycopg.connect(database_url) as conn:
        incident = create_incident(conn, IncidentInput(fingerprint, "integration-failure-evidence", ("e" * 32,)))

    evidence = [{
        "tool_name": "openobserve_search_logs",
        "status": "error",
        "error_code": "openobserve_tool_error",
        "params": {"sql": "SELECT * FROM app_logs WHERE trace_id = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'"},
        "data": None,
    }]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("WORKER_DATABASE_URL", database_url)

    def fail_investigation(_task):
        raise PermanentTaskError("holmes_tool_error", evidence=evidence)

    monkeypatch.setattr("worker.run_investigation", fail_investigation)
    try:
        assert investigate_task.run(incident["task_id"]) == "failed"
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT status, error_code, result FROM tasks WHERE id = %s", (incident["task_id"],))
                assert cursor.fetchone() == (
                    "failed",
                    "holmes_tool_error",
                    {"evidence_status": "unavailable", "evidence": evidence},
                )
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (incident["task_id"],))
                cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
                cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
                cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))


def test_outbox_delivery_exhaustion_dead_letters_and_manual_retry_resets_it(monkeypatch):
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    apply_migrations(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SESSION_SIGNING_KEY", "local-test-session-signing-key-123456")
    monkeypatch.setenv("AIOPS_DEMO_ACTIONS_ENABLED", "true")

    user_id = str(uuid.uuid4())
    user = ("outbox-operator", "operator-passphrase-456")
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, role, resource_scopes) VALUES (%s, %s, %s, 'operator', %s)",
                (user_id, user[0], hash_password(user[1]), ["order-service"]),
            )
        fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-outbox-dead-letter"))
        incident = create_incident(conn, IncidentInput(fingerprint, "integration-outbox-dead-letter"))
        conn.execute(
            """UPDATE outbox_events SET delivery_attempts = 1, available_at = now() - interval '1 second'
               WHERE idempotency_key = %s""",
            (f"investigate:{fingerprint}",),
        )

    sent = []
    try:
        assert _dispatch_once(database_url, lambda *args, **kwargs: sent.append((args, kwargs)), interval_seconds=0, max_delivery_attempts=1) == 0
        assert sent == []
        with psycopg.connect(database_url) as conn:
            assert conn.execute("SELECT status, error_code FROM tasks WHERE id = %s", (incident["task_id"],)).fetchone() == ("failed", "outbox_delivery_exhausted")
            assert conn.execute("SELECT delivery_attempts, dead_lettered_at IS NOT NULL FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",)).fetchone() == (1, True)
            assert conn.execute("SELECT event_type, details FROM audit_events WHERE task_id = %s ORDER BY id DESC LIMIT 1", (incident["task_id"],)).fetchone() == ("task.failed", {"error_code": "outbox_delivery_exhausted", "max_delivery_attempts": 1})

        class NoopDispatcher:
            def stop(self):
                pass

        monkeypatch.setattr("app.start_outbox_dispatcher", lambda *_args: NoopDispatcher())
        with TestClient(app) as client:
            login = client.post("/auth/login", json={"username": user[0], "password": user[1]})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            retried = client.post(f"/api/tasks/{incident['task_id']}/retry", headers=headers)
            assert retried.status_code == 202
            assert retried.json()["status"] == "queued"

        with psycopg.connect(database_url) as conn:
            assert conn.execute("SELECT status, error_code FROM tasks WHERE id = %s", (incident["task_id"],)).fetchone() == ("queued", None)
            assert conn.execute("SELECT delivery_attempts, dead_lettered_at, published_at, last_enqueued_at FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",)).fetchone() == (0, None, None, None)
            assert conn.execute("SELECT event_type, details FROM audit_events WHERE task_id = %s ORDER BY id DESC LIMIT 1", (incident["task_id"],)).fetchone() == ("task.manual_retry_requested", {"previous_error_code": "outbox_delivery_exhausted", "duplicate_charge_risk_acknowledged": False})
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM audit_events WHERE incident_id = %s", (incident["incident_id"],))
                cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
                cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
                cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))
                cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
