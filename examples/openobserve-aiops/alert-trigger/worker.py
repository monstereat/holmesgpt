"""Celery delivery plus PostgreSQL outbox reconciliation."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any
from urllib.parse import urlsplit

import psycopg
from celery import Celery

from broker_config import validate_broker_url
from db_config import validate_database_url
from holmes_client import HolmesClient
from tasks import (
    ClaimedTask,
    PermanentTaskError,
    RetryableTaskError,
    claim_task,
    complete_task,
    fail_task,
)

logger = logging.getLogger("holmes_aiops.worker")
local_broker_url = "redis://redis:6379/0" if os.getenv("AIOPS_ENV", "production") == "local" else ""
broker_url = validate_broker_url(os.getenv("REDIS_URL", local_broker_url), required=True)
celery_app = Celery("holmes_aiops", broker=broker_url)
celery_app.conf.update(
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
)


def _dispatch_once(database_url: str, sender: Any, *, interval_seconds: int = 20, batch_size: int = 20) -> int:
    sent = 0
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute(
                    """WITH expired AS (
                           UPDATE tasks SET status = 'failed', completed_at = now(),
                               error_code = 'worker_outcome_unknown', lease_expires_at = NULL,
                               updated_at = now()
                           WHERE status = 'running' AND lease_expires_at < now()
                           RETURNING incident_id, id
                       )
                       INSERT INTO audit_events (incident_id, task_id, event_type, details)
                       SELECT incident_id, id, 'task.failed', '{"error_code":"worker_outcome_unknown"}'::jsonb
                       FROM expired"""
                )
                cursor.execute(
                    """SELECT o.id, o.payload->>'task_id'
                       FROM outbox_events o
                       JOIN tasks t ON t.id = (o.payload->>'task_id')::uuid
                       WHERE o.event_type = 'investigation.requested'
                         AND t.status IN ('queued', 'retrying')
                         AND t.available_at <= now()
                         AND (o.last_enqueued_at IS NULL
                              OR o.last_enqueued_at < now() - (%s * interval '1 second'))
                       ORDER BY o.created_at
                       LIMIT %s FOR UPDATE OF o SKIP LOCKED""",
                    (interval_seconds, batch_size),
                )
                rows = cursor.fetchall()
                for outbox_id, task_id in rows:
                    sender("holmes_aiops.investigate", args=[task_id], task_id=str(outbox_id))
                    cursor.execute(
                        """UPDATE outbox_events
                           SET last_enqueued_at = now(), delivery_attempts = delivery_attempts + 1,
                               published_at = COALESCE(published_at, now())
                           WHERE id = %s""",
                        (outbox_id,),
                    )
                    sent += 1
    return sent


class OutboxDispatcher:
    def __init__(self, database_url: str, sender: Any = None, interval_seconds: int = 2):
        self.database_url = database_url
        self.sender = sender or celery_app.send_task
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="outbox-dispatcher", daemon=True)

    def start(self) -> "OutboxDispatcher":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _dispatch_once(self.database_url, self.sender)
            except Exception:  # Do not log broker/database details that may contain credentials.
                logger.warning("Outbox dispatch failed; pending events will be retried")
            self._stop.wait(self.interval_seconds)


def start_outbox_dispatcher(database_url: str) -> OutboxDispatcher:
    return OutboxDispatcher(database_url).start()


def _database_url() -> str:
    return validate_database_url(os.getenv("WORKER_DATABASE_URL", ""), "WORKER_DATABASE_URL", required=True)


def _holmes_client() -> HolmesClient:
    base_url = os.getenv("HOLMES_API_URL", "")
    api_key = os.getenv("HOLMES_API_KEY", "")
    if not base_url or not api_key:
        raise RuntimeError("HOLMES_API_URL and HOLMES_API_KEY are required")
    try:
        timeout_seconds = int(os.getenv("HOLMES_TIMEOUT_SECONDS", "900"))
        client = HolmesClient(base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds)
    except (ValueError, TypeError):
        raise RuntimeError("Holmes worker configuration is invalid") from None
    if os.getenv("AIOPS_ENV", "production") != "local" and urlsplit(client.base_url).scheme != "https":
        raise RuntimeError("HOLMES_API_URL must use HTTPS outside local mode")
    return client


def validate_worker_configuration() -> None:
    _database_url()
    _holmes_client()


def run_investigation(task: ClaimedTask) -> dict[str, Any]:
    try:
        client = _holmes_client()
    except RuntimeError:
        raise PermanentTaskError("holmes_configuration_missing") from None
    client.check_openobserve_toolset()
    return client.investigate({
        "task_id": task.task_id,
        "alert_name": task.alert_name,
        "trace_ids": task.trace_ids,
        "summary": task.summary,
    })


@celery_app.task(name="holmes_aiops.investigate")
def investigate_task(task_id: str) -> str:
    database_url = _database_url()
    with psycopg.connect(database_url) as conn:
        task = claim_task(conn, task_id)
    if task is None:
        return "ignored"
    try:
        result = run_investigation(task)
    except RetryableTaskError as exc:
        with psycopg.connect(database_url) as conn:
            return fail_task(conn, task, exc.code, retryable=True)
    except PermanentTaskError as exc:
        failure_result = (
            {"evidence": exc.evidence, "evidence_status": "unavailable"} if exc.evidence else None
        )
        with psycopg.connect(database_url) as conn:
            return fail_task(conn, task, exc.code, retryable=False, failure_result=failure_result)
    except Exception:
        with psycopg.connect(database_url) as conn:
            return fail_task(conn, task, "internal_error", retryable=False)
    with psycopg.connect(database_url) as conn:
        return "completed" if complete_task(conn, task, result) else "stale"
