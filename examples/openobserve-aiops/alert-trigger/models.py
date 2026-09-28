"""Typed incident-domain records shared by the local API and workers."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str
    resource_scopes: tuple[str, ...] = ()
    session_generation: int = 0


@dataclass(frozen=True)
class IncidentInput:
    fingerprint: str
    alert_name: str
    trace_ids: tuple[str, ...] = ()
    summary: dict[str, Any] = field(default_factory=dict)
    severity: str = "medium"
    resource: str = "order-service"


@dataclass(frozen=True)
class IncidentTask:
    id: str
    incident_id: str
    task_type: str
    status: TaskStatus
    attempt: int
    max_attempts: int
    created_at: datetime | None = None
