"""Database-owned task lifecycle and bounded retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb
from task_errors import PermanentTaskError, RetryableTaskError


MAX_ATTEMPTS = 4
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 300
TASK_LEASE_SECONDS = 20 * 60


@dataclass(frozen=True)
class ClaimedTask:
    task_id: str
    incident_id: str
    attempt: int
    max_attempts: int
    alert_name: str
    trace_ids: list[str]
    summary: dict[str, Any]


def retry_delay(attempt: int, *, base: int = RETRY_BASE_SECONDS, cap: int = RETRY_MAX_SECONDS) -> int:
    if attempt < 1:
        raise ValueError("attempt must be at least one")
    return min(base * (2 ** (attempt - 1)), cap)


def claim_task(conn: Any, task_id: str) -> ClaimedTask | None:
    """Atomically claim a queued task or recover a worker's expired lease."""
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """UPDATE tasks
                   SET status = 'running', attempt = attempt + 1,
                       started_at = now(), lease_expires_at = now() + (%s * interval '1 second'),
                       updated_at = now()
                   WHERE id = %s AND attempt < max_attempts AND available_at <= now()
                     AND (status IN ('queued', 'retrying')
                          OR (status = 'running' AND lease_expires_at < now()))
                   RETURNING id, incident_id, attempt, max_attempts""",
                (TASK_LEASE_SECONDS, task_id),
            )
            claimed = cursor.fetchone()
            if not claimed:
                return None
            task_uuid, incident_uuid, attempt, max_attempts = claimed
            cursor.execute(
                "SELECT alert_name, trace_ids, summary FROM incidents WHERE id = %s",
                (incident_uuid,),
            )
            incident = cursor.fetchone()
            if not incident:
                raise RuntimeError("claimed task references missing incident")
            alert_name, trace_ids, summary = incident
            return ClaimedTask(
                task_id=str(task_uuid),
                incident_id=str(incident_uuid),
                attempt=attempt,
                max_attempts=max_attempts,
                alert_name=alert_name,
                trace_ids=list(trace_ids),
                summary=summary,
            )


def complete_task(conn: Any, task: ClaimedTask, result: dict[str, Any]) -> bool:
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """UPDATE tasks SET status = 'completed', result = %s,
                   completed_at = now(), lease_expires_at = NULL, updated_at = now()
                   WHERE id = %s AND status = 'running' AND attempt = %s""",
                (Jsonb(result), task.task_id, task.attempt),
            )
            if cursor.rowcount != 1:
                return False
            cursor.execute(
                "INSERT INTO audit_events (incident_id, task_id, event_type) VALUES (%s, %s, 'task.completed')",
                (task.incident_id, task.task_id),
            )
            return True


def fail_task(conn: Any, task: ClaimedTask, code: str, *, retryable: bool) -> str:
    """Persist a safe error code and either schedule bounded retry or terminate."""
    will_retry = retryable and task.attempt < task.max_attempts
    delay = retry_delay(task.attempt) if will_retry else 0
    next_status = "retrying" if will_retry else "failed"
    event = "task.retry_scheduled" if will_retry else "task.failed"
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """UPDATE tasks SET status = %s, error_code = %s,
                   available_at = now() + (%s * interval '1 second'),
                   completed_at = CASE WHEN %s = 'failed' THEN now() ELSE NULL END,
                   lease_expires_at = NULL, updated_at = now()
                   WHERE id = %s AND status = 'running' AND attempt = %s""",
                (next_status, code[:64], delay, next_status, task.task_id, task.attempt),
            )
            if cursor.rowcount != 1:
                return "stale"
            cursor.execute(
                "INSERT INTO audit_events (incident_id, task_id, event_type, details) VALUES (%s, %s, %s, %s)",
                (task.incident_id, task.task_id, event, Jsonb({"error_code": code[:64], "retry_delay_seconds": delay})),
            )
    return next_status
