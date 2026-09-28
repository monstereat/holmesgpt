"""Focused integration coverage for PostgreSQL outbox dispatch reliability."""

import os
import threading
import uuid
from urllib.parse import urlparse

import psycopg
import pytest

from migration_runner import apply_migrations
from models import IncidentInput
from store import create_incident, stable_fingerprint
from worker import _dispatch_once


def _test_database_url() -> str:
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    parsed = urlparse(database_url)
    if parsed.hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    if parsed.path != "/aiops_test":
        pytest.fail("outbox integration tests only permit the isolated aiops_test database")
    apply_migrations(database_url)
    return database_url


def _create_outbox_task(database_url: str, summary: str) -> dict[str, str]:
    fingerprint = stable_fingerprint(IncidentInput(uuid.uuid4().hex * 2, summary))
    with psycopg.connect(database_url) as conn:
        incident = create_incident(conn, IncidentInput(fingerprint, summary))
    return {"fingerprint": fingerprint, **incident}


def _cleanup(database_url: str, incident: dict[str, str]) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM audit_events WHERE incident_id = %s", (incident["incident_id"],))
            cursor.execute("DELETE FROM outbox_events WHERE idempotency_key = %s", (f"investigate:{incident['fingerprint']}",))
            cursor.execute("DELETE FROM tasks WHERE id = %s", (incident["task_id"],))
            cursor.execute("DELETE FROM incidents WHERE id = %s", (incident["incident_id"],))


def test_publish_failure_is_retried_after_dispatch_backoff():
    database_url = _test_database_url()
    incident = _create_outbox_task(database_url, "outbox-publish-retry")
    calls = []

    def failing_sender(*args, **kwargs):
        calls.append((args, kwargs))
        raise ConnectionError("broker unavailable")

    try:
        assert _dispatch_once(database_url, failing_sender, interval_seconds=60) == 0
        assert _dispatch_once(database_url, failing_sender, interval_seconds=60) == 0
        assert len(calls) == 1
        with psycopg.connect(database_url) as conn:
            attempts, published_at, last_enqueued_at = conn.execute(
                "SELECT delivery_attempts, published_at, last_enqueued_at FROM outbox_events WHERE idempotency_key = %s",
                (f"investigate:{incident['fingerprint']}",),
            ).fetchone()
            assert attempts == 1
            assert published_at is None
            assert last_enqueued_at is not None

        with psycopg.connect(database_url) as conn:
            conn.execute(
                "UPDATE outbox_events SET last_enqueued_at = now() - interval '10 minutes' WHERE idempotency_key = %s",
                (f"investigate:{incident['fingerprint']}",),
            )

        accepted = []
        assert _dispatch_once(
            database_url,
            lambda *args, **kwargs: accepted.append((args, kwargs)),
            interval_seconds=60,
        ) == 1
        assert len(accepted) == 1
        with psycopg.connect(database_url) as conn:
            attempts, published_at = conn.execute(
                "SELECT delivery_attempts, published_at FROM outbox_events WHERE idempotency_key = %s",
                (f"investigate:{incident['fingerprint']}",),
            ).fetchone()
            assert attempts == 2
            assert published_at is not None
    finally:
        _cleanup(database_url, incident)


def test_concurrent_dispatchers_do_not_enqueue_same_outbox_row_twice():
    database_url = _test_database_url()
    incident = _create_outbox_task(database_url, "outbox-concurrent-dispatch")
    sender_entered = threading.Event()
    release_sender = threading.Event()
    first_calls = []
    first_result = []
    first_error = []

    def blocking_sender(*args, **kwargs):
        first_calls.append((args, kwargs))
        sender_entered.set()
        if not release_sender.wait(timeout=10):
            raise TimeoutError("test sender was not released")

    def dispatch_first():
        try:
            first_result.append(_dispatch_once(database_url, blocking_sender, interval_seconds=60))
        except Exception as exc:  # propagate thread failures to the test thread
            first_error.append(exc)

    thread = threading.Thread(target=dispatch_first)
    try:
        try:
            thread.start()
            assert sender_entered.wait(timeout=10), "first dispatcher did not claim the outbox row"
            second_calls = []
            second_result = _dispatch_once(
                database_url,
                lambda *args, **kwargs: second_calls.append((args, kwargs)),
                interval_seconds=60,
            )
            assert second_result == 0
            assert second_calls == []
        finally:
            release_sender.set()
            thread.join(timeout=10)

        assert not thread.is_alive()
        assert first_error == []
        assert first_result == [1]
        assert len(first_calls) == 1
        with psycopg.connect(database_url) as conn:
            assert conn.execute(
                "SELECT delivery_attempts, published_at IS NOT NULL FROM outbox_events WHERE idempotency_key = %s",
                (f"investigate:{incident['fingerprint']}",),
            ).fetchone() == (1, True)
    finally:
        _cleanup(database_url, incident)
