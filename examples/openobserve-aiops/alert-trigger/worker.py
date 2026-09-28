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
from db_config import connect_database, validate_database_url
from holmes_client import HolmesClient
from tasks import (
    ClaimedTask,
    PermanentTaskError,
    RetryableTaskError,
    claim_task,
    complete_task,
    fail_task,
    TASK_LEASE_SECONDS,
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
    broker_transport_options={"visibility_timeout": 1800},
    result_backend_transport_options={"visibility_timeout": 1800},
    visibility_timeout=1800,
)


def _dispatch_once(
    database_url: str,
    sender: Any,
    *,
    interval_seconds: int = 20,
    batch_size: int = 20,
    max_delivery_attempts: int = 20,
) -> int:
    sent = 0
    with connect_database(database_url) as conn:
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
                    """WITH failed AS (
                           UPDATE tasks t
                           SET status = 'failed', error_code = 'outbox_delivery_exhausted',
                               completed_at = now(), lease_expires_at = NULL, updated_at = now()
                           WHERE t.status IN ('queued', 'retrying') AND t.available_at <= now()
                             AND EXISTS (
                                 SELECT 1 FROM outbox_events o
                                 WHERE o.event_type = 'investigation.requested'
                                   AND o.payload->>'task_id' = t.id::text
                                   AND o.dead_lettered_at IS NULL
                                   AND o.delivery_attempts >= %s AND o.available_at <= now()
                             )
                           RETURNING t.id, t.incident_id
                       ), dead_lettered AS (
                           UPDATE outbox_events o SET dead_lettered_at = now()
                           FROM failed f
                           WHERE o.event_type = 'investigation.requested'
                             AND o.payload->>'task_id' = f.id::text
                             AND o.dead_lettered_at IS NULL
                           RETURNING f.id AS task_id, f.incident_id
                       )
                       INSERT INTO audit_events (incident_id, task_id, event_type, details)
                       SELECT incident_id, task_id, 'task.failed',
                              jsonb_build_object('error_code', 'outbox_delivery_exhausted',
                                                 'max_delivery_attempts', %s)
                       FROM dead_lettered""",
                    (max_delivery_attempts, max_delivery_attempts),
                )
                cursor.execute(
                    """WITH due AS (
                           SELECT o.id, o.payload->>'task_id' AS task_id
                           FROM outbox_events o
                           JOIN tasks t ON t.id = (o.payload->>'task_id')::uuid
                           WHERE o.event_type = 'investigation.requested'
                             AND o.dead_lettered_at IS NULL
                             AND t.status IN ('queued', 'retrying')
                             AND t.available_at <= now()
                             AND o.available_at <= now()
                             AND (
                                 o.last_enqueued_at IS NULL
                                 OR o.last_enqueued_at < now() - make_interval(
                                     secs => (%s * LEAST(
                                         power(2.0, LEAST(GREATEST(o.delivery_attempts - 1, 0), 4)),
                                         15
                                     ))::double precision * (
                                         0.8 + mod(
                                             hashtextextended(o.id::text || ':' || o.delivery_attempts::text, 0)
                                             & 9223372036854775807,
                                             1001
                                         )::double precision / 5000.0
                                     )
                                 )
                             )
                           ORDER BY o.created_at
                           LIMIT %s FOR UPDATE OF o SKIP LOCKED
                       )
                       UPDATE outbox_events o
                       SET last_enqueued_at = now(), delivery_attempts = o.delivery_attempts + 1
                       FROM due
                       WHERE o.id = due.id
                       RETURNING o.id, due.task_id""",
                    (interval_seconds, batch_size),
                )
                rows = cursor.fetchall()
    for outbox_id, task_id in rows:
        try:
            sender("holmes_aiops.investigate", args=[task_id], task_id=str(outbox_id))
        except Exception:
            logger.warning("Outbox event publish failed; retry is scheduled")
            continue
        sent += 1
        try:
            with connect_database(database_url) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE outbox_events SET published_at = COALESCE(published_at, now()) WHERE id = %s",
                        (outbox_id,),
                    )
        except psycopg.Error:
            logger.warning("Outbox publish was accepted but its database acknowledgement failed")
    return sent


class OutboxDispatcher:
    def __init__(self, database_url: str, sender: Any = None, interval_seconds: int = 2, max_delivery_attempts: int = 20):
        self.database_url = database_url
        self.sender = sender or celery_app.send_task
        self.interval_seconds = interval_seconds
        self.max_delivery_attempts = max_delivery_attempts
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
                _dispatch_once(
                    self.database_url,
                    self.sender,
                    max_delivery_attempts=self.max_delivery_attempts,
                )
            except Exception:  # Do not log broker/database details that may contain credentials.
                logger.warning("Outbox dispatch failed; pending events will be retried")
            self._stop.wait(self.interval_seconds)


def start_outbox_dispatcher(database_url: str) -> OutboxDispatcher:
    return OutboxDispatcher(database_url, max_delivery_attempts=_max_outbox_delivery_attempts()).start()


def _max_outbox_delivery_attempts() -> int:
    configured = os.getenv("AIOPS_OUTBOX_MAX_DELIVERY_ATTEMPTS", "")
    if not configured and os.getenv("AIOPS_ENV", "production") == "local":
        return 20
    try:
        attempts = int(configured)
    except (ValueError, TypeError):
        raise RuntimeError("AIOPS_OUTBOX_MAX_DELIVERY_ATTEMPTS must be a positive integer") from None
    if attempts < 1:
        raise RuntimeError("AIOPS_OUTBOX_MAX_DELIVERY_ATTEMPTS must be a positive integer")
    return attempts


def _database_url() -> str:
    return validate_database_url(os.getenv("WORKER_DATABASE_URL", ""), "WORKER_DATABASE_URL", required=True)


def _holmes_client() -> HolmesClient:
    base_url = os.getenv("HOLMES_API_URL", "")
    api_key = os.getenv("HOLMES_API_KEY", "")
    if not base_url or not api_key:
        raise RuntimeError("HOLMES_API_URL and HOLMES_API_KEY are required")
    try:
        timeout_seconds = int(os.getenv("HOLMES_TIMEOUT_SECONDS", "900"))
        if timeout_seconds >= TASK_LEASE_SECONDS:
            raise RuntimeError("HOLMES_TIMEOUT_SECONDS must be shorter than the task lease")
        client = HolmesClient(base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds)
    except (ValueError, TypeError):
        raise RuntimeError("Holmes worker configuration is invalid") from None
    if os.getenv("AIOPS_ENV", "production") != "local" and urlsplit(client.base_url).scheme != "https":
        raise RuntimeError("HOLMES_API_URL must use HTTPS outside local mode")
    return client


def validate_worker_configuration() -> None:
    _database_url()
    _holmes_client()
    _max_outbox_delivery_attempts()


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
    with connect_database(database_url) as conn:
        task = claim_task(conn, task_id)
    if task is None:
        return "ignored"
    try:
        result = run_investigation(task)
    except RetryableTaskError as exc:
        with connect_database(database_url) as conn:
            return fail_task(conn, task, exc.code, retryable=True)
    except PermanentTaskError as exc:
        failure_result = {}
        if exc.evidence:
            failure_result.update({"evidence": exc.evidence, "evidence_status": "unavailable"})
        if exc.possible_duplicate_charge:
            failure_result["possible_duplicate_charge"] = True
        with connect_database(database_url) as conn:
            return fail_task(
                conn,
                task,
                exc.code,
                retryable=False,
                failure_result=failure_result or None,
            )
    except Exception:
        with connect_database(database_url) as conn:
            return fail_task(conn, task, "internal_error", retryable=False)
    with connect_database(database_url) as conn:
        return "completed" if complete_task(conn, task, result) else "stale"
