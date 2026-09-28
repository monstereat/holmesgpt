"""PostgreSQL persistence for incident creation and its transactional outbox."""

import hashlib
import json
import re
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from models import IncidentInput


class TaskQueueAtCapacity(Exception):
    """Raised when admitting an alert would exceed the configured task queue cap."""


class IncidentResourceConflict(Exception):
    """Raised when a source fingerprint is reused across resource scopes."""


RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,99}$")


def stable_fingerprint(alert: IncidentInput) -> str:
    trace_ids = sorted(set(alert.trace_ids))
    if trace_ids:
        material = [alert.alert_name, trace_ids]
        if alert.resource != "order-service":
            material.insert(0, alert.resource)
    else:
        summary = {key: value for key, value in alert.summary.items() if key != "alert_trigger_time_str"}
        material = [alert.alert_name, summary]
        if alert.resource != "order-service":
            material.insert(0, alert.resource)
    encoded = json.dumps(material, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def create_incident(
    conn: Any,
    alert: IncidentInput,
    *,
    max_pending_tasks: int | None = None,
) -> dict[str, Any]:
    """Create one incident, task and outbox event atomically for a fingerprint."""
    if len(alert.fingerprint) != 64 or any(char not in "0123456789abcdef" for char in alert.fingerprint):
        raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")
    if not RESOURCE_RE.fullmatch(alert.resource):
        raise ValueError("resource must be a normalized service key")
    if max_pending_tasks is not None and max_pending_tasks < 1:
        raise ValueError("max_pending_tasks must be positive")
    incident_id = uuid.uuid4()
    task_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    task_key = f"investigate:{alert.fingerprint}"
    with conn.transaction():
        with conn.cursor() as cursor:
            if max_pending_tasks is not None:
                # Serialize admission so concurrent API replicas cannot exceed the pending-task cap.
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (1095329616, 1396928336))
                cursor.execute(
                    "SELECT id, status, created_at, resource FROM incidents WHERE fingerprint = %s",
                    (alert.fingerprint,),
                )
                row = cursor.fetchone()
                if row is not None and row[3] != alert.resource:
                    raise IncidentResourceConflict
                if row is None:
                    cursor.execute(
                        "SELECT count(*) FROM tasks WHERE status IN ('queued', 'running', 'retrying')"
                    )
                    pending_count = cursor.fetchone()[0]
                    if pending_count >= max_pending_tasks:
                        raise TaskQueueAtCapacity
            cursor.execute(
                """INSERT INTO incidents (id, fingerprint, alert_name, trace_ids, summary, severity, resource)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (fingerprint) DO NOTHING
                   RETURNING id, status, created_at, resource""",
                (incident_id, alert.fingerprint, alert.alert_name, list(alert.trace_ids), Jsonb(alert.summary), alert.severity, alert.resource),
            )
            inserted_row = cursor.fetchone()
            created = inserted_row is not None
            if inserted_row is not None:
                row = inserted_row
            if not created:
                cursor.execute(
                    "SELECT id, status, created_at, resource FROM incidents WHERE fingerprint = %s",
                    (alert.fingerprint,),
                )
                row = cursor.fetchone()
                if row and row[3] != alert.resource:
                    raise IncidentResourceConflict
            if row is None:
                raise RuntimeError("incident disappeared during idempotent admission")
            incident_id_value, status, created_at, _resource = row
            if created:
                cursor.execute(
                    """INSERT INTO tasks (id, incident_id, task_type, idempotency_key, max_attempts)
                       VALUES (%s, %s, 'investigation', %s, 4)""",
                    (task_id, incident_id_value, task_key),
                )
                cursor.execute(
                    """INSERT INTO outbox_events
                       (id, aggregate_type, aggregate_id, event_type, payload, idempotency_key)
                       VALUES (%s, 'incident', %s, 'investigation.requested', %s, %s)""",
                    (outbox_id, incident_id_value, Jsonb({"task_id": str(task_id)}), task_key),
                )
            cursor.execute("SELECT id FROM tasks WHERE incident_id = %s AND task_type = 'investigation'", (incident_id_value,))
            task_row = cursor.fetchone()
            return {
                "incident_id": str(incident_id_value),
                "task_id": str(task_row[0]) if task_row else None,
                "status": status,
                "created_at": created_at,
                "created": created,
            }
