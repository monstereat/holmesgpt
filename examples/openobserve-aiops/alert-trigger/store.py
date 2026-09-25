"""PostgreSQL persistence for incident creation and its transactional outbox."""

import hashlib
import json
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from models import IncidentInput


def stable_fingerprint(alert: IncidentInput) -> str:
    trace_ids = sorted(set(alert.trace_ids))
    if trace_ids:
        material = [alert.alert_name, trace_ids]
    else:
        summary = {key: value for key, value in alert.summary.items() if key != "alert_trigger_time_str"}
        material = [alert.alert_name, summary]
    encoded = json.dumps(material, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def create_incident(conn: Any, alert: IncidentInput) -> dict[str, Any]:
    """Create one incident, task and outbox event atomically for a fingerprint."""
    if len(alert.fingerprint) != 64 or any(char not in "0123456789abcdef" for char in alert.fingerprint):
        raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")
    incident_id = uuid.uuid4()
    task_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    task_key = f"investigate:{alert.fingerprint}"
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO incidents (id, fingerprint, alert_name, trace_ids, summary)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (fingerprint) DO NOTHING
                   RETURNING id, status, created_at""",
                (incident_id, alert.fingerprint, alert.alert_name, list(alert.trace_ids), Jsonb(alert.summary)),
            )
            row = cursor.fetchone()
            created = row is not None
            if not created:
                cursor.execute(
                    "SELECT id, status, created_at FROM incidents WHERE fingerprint = %s",
                    (alert.fingerprint,),
                )
                row = cursor.fetchone()
            incident_id_value, status, created_at = row
            if created:
                cursor.execute(
                    """INSERT INTO tasks (id, incident_id, task_type, idempotency_key)
                       VALUES (%s, %s, 'investigation', %s)""",
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
