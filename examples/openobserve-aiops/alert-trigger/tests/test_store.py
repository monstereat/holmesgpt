import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import psycopg
import pytest

from models import IncidentInput
from store import TaskQueueAtCapacity, create_incident, stable_fingerprint


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        self.conn.queries.append((query, params))
        if query.startswith("SELECT id, status, created_at FROM incidents WHERE fingerprint"):
            self.row = None if self.conn.new_incident else (self.conn.incident_id, "open", "now")
        elif query.startswith("SELECT count(*) FROM tasks"):
            self.row = (self.conn.pending_count,)
        elif query.startswith("INSERT INTO incidents"):
            self.row = (params[0], "open", "now") if self.conn.new_incident else None
        elif "WHERE fingerprint" in query:
            self.row = (self.conn.incident_id, "open", "now")
        elif query.startswith("SELECT id FROM tasks"):
            self.row = (self.conn.task_id,)

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, *, new_incident):
        self.new_incident = new_incident
        self.incident_id = "existing-incident"
        self.task_id = "existing-task"
        self.pending_count = 0
        self.queries = []

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def transaction(self):
        return self.Transaction()

    def cursor(self):
        return FakeCursor(self)


def test_fingerprint_is_stable_and_ignores_trigger_timestamp():
    base = IncidentInput("0" * 64, "order-500", ("b" * 32, "a" * 32), {"count": 2, "alert_trigger_time_str": "first"})
    repeated = IncidentInput("0" * 64, "order-500", ("a" * 32, "b" * 32), {"count": 2, "alert_trigger_time_str": "later"})
    assert stable_fingerprint(base) == stable_fingerprint(repeated)


def test_fingerprint_uses_trace_ids_when_available_and_summary_otherwise():
    traced = IncidentInput("0" * 64, "order-500", ("a" * 32,), {"count": 1})
    traced_again = IncidentInput("0" * 64, "order-500", ("a" * 32,), {"count": 9})
    no_trace = IncidentInput("0" * 64, "order-500", (), {"count": 1, "alert_trigger_time_str": "first"})
    no_trace_again = IncidentInput("0" * 64, "order-500", (), {"count": 1, "alert_trigger_time_str": "later"})
    no_trace_changed = IncidentInput("0" * 64, "order-500", (), {"count": 2})
    assert stable_fingerprint(traced) == stable_fingerprint(traced_again)
    assert stable_fingerprint(no_trace) == stable_fingerprint(no_trace_again)
    assert stable_fingerprint(no_trace) != stable_fingerprint(no_trace_changed)


def test_new_incident_writes_task_and_outbox_in_one_transaction():
    conn = FakeConnection(new_incident=True)
    alert = IncidentInput("a" * 64, "order-500", ("b" * 32,), {"count": 1})
    result = create_incident(conn, alert)
    assert result["created"] is True
    statements = [query for query, _ in conn.queries]
    assert sum(query.startswith("INSERT INTO") for query in statements) == 3
    assert "INSERT INTO tasks" in statements[1]
    assert "INSERT INTO outbox_events" in statements[2]


def test_duplicate_incident_reuses_existing_task_and_writes_no_outbox():
    conn = FakeConnection(new_incident=False)
    result = create_incident(conn, IncidentInput("c" * 64, "order-500"))
    assert result["created"] is False
    assert result["incident_id"] == "existing-incident"
    assert result["task_id"] == "existing-task"
    assert not any("INSERT INTO tasks" in query or "INSERT INTO outbox_events" in query for query, _ in conn.queries)


def test_invalid_fingerprint_is_rejected_before_database_access():
    conn = FakeConnection(new_incident=True)
    try:
        create_incident(conn, IncidentInput("not-a-fingerprint", "bad"))
    except ValueError as error:
        assert "SHA-256" in str(error)
    else:
        raise AssertionError("invalid fingerprint was accepted")
    assert not conn.queries


def test_pending_task_capacity_admits_up_to_limit_and_preserves_duplicate_idempotency():
    conn = FakeConnection(new_incident=True)
    result = create_incident(
        conn,
        IncidentInput("a" * 64, "order-500"),
        max_pending_tasks=1,
    )

    assert result["created"] is True
    assert any("pg_advisory_xact_lock" in query for query, _ in conn.queries)
    assert any("SELECT count(*) FROM tasks" in query for query, _ in conn.queries)

    duplicate_conn = FakeConnection(new_incident=False)
    duplicate_conn.pending_count = 1
    duplicate = create_incident(
        duplicate_conn,
        IncidentInput("c" * 64, "order-500"),
        max_pending_tasks=1,
    )
    assert duplicate["created"] is False
    assert not any("SELECT count(*) FROM tasks" in query for query, _ in duplicate_conn.queries)


def test_pending_task_capacity_rejects_new_alert_atomically():
    conn = FakeConnection(new_incident=True)
    conn.pending_count = 1

    with pytest.raises(TaskQueueAtCapacity):
        create_incident(conn, IncidentInput("a" * 64, "order-500"), max_pending_tasks=1)

    assert not any(query.startswith("INSERT INTO") for query, _ in conn.queries)


def test_postgres_migration_and_idempotency_on_local_test_database():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")

    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-order-500", ("a" * 32,)))
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_migrations WHERE version = '0001_incidents_tasks'")
            assert cursor.fetchone() == ("0001_incidents_tasks",)
            cursor.execute("SELECT version FROM schema_migrations WHERE version = '0006_audit_events_append_only'")
            assert cursor.fetchone() == ("0006_audit_events_append_only",)
            cursor.execute("SELECT version FROM schema_migrations WHERE version = '0007_task_duration_metrics_index'")
            assert cursor.fetchone() == ("0007_task_duration_metrics_index",)
        alert = IncidentInput(fingerprint, "integration-order-500", ("a" * 32,), {"count": 1})
        first = create_incident(conn, alert)
        repeated = create_incident(conn, alert)
        assert first["created"] is True
        assert repeated["created"] is False
        assert repeated["incident_id"] == first["incident_id"]
        assert repeated["task_id"] == first["task_id"]
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            assert cursor.fetchone() == (1,)
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{fingerprint}",))
            cursor.execute("DELETE FROM tasks WHERE incident_id = %s", (first["incident_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (first["incident_id"],))


def test_postgres_rolls_back_incident_and_task_if_outbox_insert_fails():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")

    fingerprint = stable_fingerprint(IncidentInput("0" * 64, "integration-rollback", ("c" * 32,)))
    idempotency_key = f"investigate:{fingerprint}"
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO outbox_events
                   (id, aggregate_type, aggregate_id, event_type, payload, idempotency_key)
                   VALUES (gen_random_uuid(), 'test', gen_random_uuid(), 'test', '{}'::jsonb, %s)""",
                (idempotency_key,),
            )
        with pytest.raises(psycopg.errors.UniqueViolation):
            create_incident(conn, IncidentInput(fingerprint, "integration-rollback", ("c" * 32,)))
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM incidents WHERE fingerprint = %s", (fingerprint,))
            assert cursor.fetchone() == (0,)
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (idempotency_key,))


def test_postgres_pending_task_cap_is_atomic_across_concurrent_webhooks():
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    if urlparse(database_url).hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")

    alerts = []
    for _ in range(2):
        candidate = IncidentInput("0" * 64, f"capacity-admission-{os.urandom(8).hex()}")
        alerts.append(IncidentInput(stable_fingerprint(candidate), candidate.alert_name))
    barrier = threading.Barrier(len(alerts))

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM tasks WHERE status IN ('queued', 'running', 'retrying')")
            capacity = cursor.fetchone()[0] + 1

    def attempt(alert):
        barrier.wait(timeout=5)
        with psycopg.connect(database_url) as conn:
            try:
                return create_incident(conn, alert, max_pending_tasks=capacity)
            except TaskQueueAtCapacity:
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, alerts))
        admitted = [result for result in results if result is not None]
        assert len(admitted) == 1
        admitted_alert = alerts[results.index(admitted[0])]
        with psycopg.connect(database_url) as conn:
            duplicate = create_incident(conn, admitted_alert, max_pending_tasks=capacity)
        assert duplicate["created"] is False
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                for alert in alerts:
                    cursor.execute("SELECT id FROM incidents WHERE fingerprint = %s", (alert.fingerprint,))
                    row = cursor.fetchone()
                    if row:
                        cursor.execute("DELETE FROM outbox_events WHERE aggregate_id = %s", (row[0],))
                        cursor.execute("DELETE FROM tasks WHERE incident_id = %s", (row[0],))
                        cursor.execute("DELETE FROM incidents WHERE id = %s", (row[0],))
