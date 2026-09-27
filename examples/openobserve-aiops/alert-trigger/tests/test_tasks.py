import os
from urllib.parse import urlparse

import psycopg
import pytest

from migration_runner import apply_migrations
from models import IncidentInput
from store import create_incident, stable_fingerprint
from task_errors import PermanentTaskError, RetryableTaskError
from tasks import claim_task, fail_task, retry_delay
from worker import investigate_task, run_investigation


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
