import os
from urllib.parse import urlparse

import psycopg
import pytest

from app import apply_migrations
from models import IncidentInput
from store import create_incident, stable_fingerprint
from tasks import claim_task, fail_task, retry_delay


def test_retry_delay_is_bounded_exponential():
    assert [retry_delay(n) for n in (1, 2, 3, 4, 5, 8)] == [5, 10, 20, 40, 80, 300]
    with pytest.raises(ValueError):
        retry_delay(0)


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
            cursor.execute("UPDATE tasks SET available_at = now() - interval '1 second', max_attempts = 1 WHERE id = %s", (result["task_id"],))
        claimed = claim_task(conn, result["task_id"])
        assert claimed is not None and claimed.attempt == 1
        assert claim_task(conn, result["task_id"]) is None
        assert fail_task(conn, claimed, "upstream_unavailable", retryable=True) == "failed"
        with conn.cursor() as cursor:
            cursor.execute("SELECT status, attempt, error_code FROM tasks WHERE id = %s", (result["task_id"],))
            assert cursor.fetchone() == ("failed", 1, "upstream_unavailable")
            cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (result["task_id"],))
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (result["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (result["incident_id"],))
