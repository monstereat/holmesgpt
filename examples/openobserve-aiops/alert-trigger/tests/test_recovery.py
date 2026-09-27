import os
from urllib.parse import urlparse

import psycopg
import pytest

from migration_runner import apply_migrations
from models import IncidentInput
from store import create_incident, stable_fingerprint
from tasks import claim_task
from worker import _dispatch_once


def _local_database_url():
    value = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not value:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(value).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    apply_migrations(value)
    return value


def test_outbox_requeues_after_broker_loss_and_worker_claim_is_idempotent():
    database_url = _local_database_url()
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-recovery", ("f" * 32,)))
    with psycopg.connect(database_url) as conn:
        incident = create_incident(conn, IncidentInput(fingerprint, "integration-recovery", ("f" * 32,)))
    sent = []
    assert _dispatch_once(database_url, lambda *args, **kwargs: sent.append((args, kwargs)), interval_seconds=0) == 1
    assert _dispatch_once(database_url, lambda *args, **kwargs: sent.append((args, kwargs)), interval_seconds=0) == 1
    assert len(sent) == 2
    with psycopg.connect(database_url) as conn:
        claimed = claim_task(conn, incident["task_id"])
        assert claimed is not None
        assert claim_task(conn, incident["task_id"]) is None
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))


def test_expired_worker_lease_fails_closed_without_duplicate_dispatch():
    database_url = _local_database_url()
    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-expired", ("1" * 32,)))
    with psycopg.connect(database_url) as conn:
        incident = create_incident(conn, IncidentInput(fingerprint, "integration-expired", ("1" * 32,)))
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE tasks SET status = 'running', attempt = 1, lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (incident["task_id"],),
            )
    sent = []
    with psycopg.connect(database_url) as conn:
        assert claim_task(conn, incident["task_id"]) is None
    assert _dispatch_once(database_url, lambda *args, **kwargs: sent.append(args), interval_seconds=0) == 0
    assert sent == []
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT status, error_code FROM tasks WHERE id = %s", (incident["task_id"],))
            assert cursor.fetchone() == ("failed", "worker_outcome_unknown")
            cursor.execute(
                "SELECT event_type, details FROM audit_events WHERE task_id = %s ORDER BY id DESC LIMIT 1",
                (incident["task_id"],),
            )
            assert cursor.fetchone() == (
                "task.failed",
                {"error_code": "worker_outcome_unknown"},
            )
            cursor.execute("DELETE FROM audit_events WHERE task_id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))
