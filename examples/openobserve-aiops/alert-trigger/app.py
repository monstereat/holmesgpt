"""Authenticated webhook API and local database migration lifecycle."""

import hmac
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from psycopg.types.json import Jsonb

from action_client import ActionServiceError, OrderActionClient
from auth import ROLE_PERMISSIONS, create_session, hash_password, parse_session, require_permission, verify_password
from models import IncidentInput, Principal
from store import create_incident
from trigger import MAX_BODY_BYTES, normalize_alert
from worker import start_outbox_dispatcher

PUBLIC_PATH = Path(__file__).parent / "public"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ApprovalRequest(BaseModel):
    action: str = Field(pattern="^set-chaos-mode$")
    resource: str = Field(pattern="^order-service$")
    enabled: bool

    model_config = {"extra": "forbid"}


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")

    model_config = {"extra": "forbid"}


class RetrospectiveRequest(BaseModel):
    impact: str = Field(default="", max_length=5000)
    root_cause: str = Field(default="", max_length=5000)
    resolution: str = Field(default="", max_length=5000)
    action_items: list[str] = Field(default_factory=list, max_length=20)
    reviewed: bool = False

    model_config = {"extra": "forbid"}

    @field_validator("action_items")
    @classmethod
    def validate_action_items(cls, items: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in items):
            raise ValueError("Action items must contain 1 to 500 characters")
        return [item.strip() for item in items]


def _seed_test_users(database_url: str, raw_users: str) -> None:
    users = json.loads(raw_users)
    if not isinstance(users, list) or not 1 <= len(users) <= 16:
        raise ValueError("AIOPS_TEST_USERS_JSON must contain 1 to 16 users")
    seen: set[str] = set()
    prepared = []
    for item in users:
        if not isinstance(item, dict):
            raise ValueError("AIOPS_TEST_USERS_JSON entries must be objects")
        username = item.get("username")
        password = item.get("password")
        role = item.get("role")
        scopes = item.get("resource_scopes")
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username)
            or username in seen
            or not isinstance(password, str)
            or not isinstance(role, str)
            or role not in ROLE_PERMISSIONS
            or not isinstance(scopes, list)
            or not scopes
            or not all(isinstance(scope, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", scope) for scope in scopes)
        ):
            raise ValueError("AIOPS_TEST_USERS_JSON contains an invalid user")
        seen.add(username)
        user_id = str(uuid.UUID(item["id"])) if item.get("id") else str(uuid.uuid4())
        prepared.append((user_id, username, hash_password(password), role, scopes))
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            for user in prepared:
                cursor.execute(
                    """INSERT INTO users (id, username, password_hash, role, resource_scopes, active)
                       VALUES (%s, %s, %s, %s, %s, TRUE)
                       ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash,
                           role = EXCLUDED.role, resource_scopes = EXCLUDED.resource_scopes, active = TRUE""",
                    user,
                )


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "")
    if not value:
        raise HTTPException(status_code=503, detail="Incident database is not configured")
    return value


def _test_users_configuration() -> str:
    raw_users = os.getenv("AIOPS_TEST_USERS_JSON", "")
    if raw_users and os.getenv("AIOPS_ENV") != "local":
        raise RuntimeError("AIOPS_TEST_USERS_JSON is only permitted when AIOPS_ENV=local")
    return raw_users


def _load_principal(authorization: str | None) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    key = os.getenv("SESSION_SIGNING_KEY", "")
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="Session authentication is not configured")
    try:
        token_principal = parse_session(authorization[7:], key)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, role, resource_scopes FROM users WHERE id = %s::uuid AND active = TRUE",
                (token_principal.user_id,),
            )
            user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return Principal(str(user[0]), user[1], user[2], tuple(user[3]))


def _authorize(principal, permission: str, resource: str) -> None:
    try:
        require_permission(principal, permission, resource)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden") from None


def _order_action_client() -> OrderActionClient:
    try:
        return OrderActionClient(
            os.getenv("ORDER_SERVICE_URL", "http://order-service:8080"),
            os.getenv("ORDER_ACTION_SERVICE_TOKEN", ""),
        )
    except ValueError:
        raise HTTPException(status_code=503, detail="Demo action service is not configured") from None


def _require_demo_actions_enabled() -> None:
    if os.getenv("AIOPS_DEMO_ACTIONS_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=503, detail="Demo remediation actions are disabled")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    raw_users = _test_users_configuration()
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        if raw_users:
            _seed_test_users(database_url, raw_users)
        dispatcher = start_outbox_dispatcher()
        try:
            yield
        finally:
            dispatcher.stop()
    else:
        yield


app = FastAPI(title="Holmes AIOps Incident Service", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def workbench() -> FileResponse:
    return FileResponse(PUBLIC_PATH / "incidents.html")


@app.post("/auth/login")
def login(body: LoginRequest) -> dict[str, str]:
    key = os.getenv("SESSION_SIGNING_KEY", "")
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="Session authentication is not configured")
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, role, resource_scopes FROM users WHERE username = %s AND active = TRUE",
                (body.username,),
            )
            user = cursor.fetchone()
    if not user or not verify_password(body.password, user[2]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_session(Principal(str(user[0]), user[1], user[3], tuple(user[4])), key)
    return {"access_token": token, "token_type": "Bearer"}


@app.get("/auth/me")
def who_am_i(authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization)
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "resource_scopes": list(principal.resource_scopes),
    }


@app.get("/api/incidents")
def list_incidents(
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=100),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    principal = _load_principal(authorization)
    _authorize(principal, "incident:read", "order-service")
    if status and status not in {"open", "investigating", "awaiting_approval", "resolved", "closed"}:
        raise HTTPException(status_code=400, detail="Invalid incident status")
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cursor:
            if status:
                cursor.execute(
                    """SELECT id, alert_name, trace_ids, status, created_at, updated_at
                       FROM incidents WHERE status = %s ORDER BY created_at DESC LIMIT %s""",
                    (status, limit),
                )
            else:
                cursor.execute(
                    """SELECT id, alert_name, trace_ids, status, created_at, updated_at
                       FROM incidents ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
            rows = cursor.fetchall()
    return {"items": [
        {"id": str(row[0]), "alert_name": row[1], "trace_ids": row[2], "status": row[3], "created_at": row[4].isoformat(), "updated_at": row[5].isoformat()}
        for row in rows
    ], "limit": limit}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization)
    _authorize(principal, "incident:read", "order-service")
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, alert_name, trace_ids, summary, status, created_at, updated_at FROM incidents WHERE id = %s",
                (incident_uuid,),
            )
            incident = cursor.fetchone()
            if not incident:
                raise HTTPException(status_code=404, detail="Incident not found")
            cursor.execute(
                """SELECT id, task_type, status, attempt, max_attempts, result, error_code,
                          created_at, updated_at FROM tasks WHERE incident_id = %s ORDER BY created_at""",
                (incident_uuid,),
            )
            tasks = cursor.fetchall()
            cursor.execute(
                """SELECT e.event_type, e.details, e.created_at, u.username
                   FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id
                   WHERE e.incident_id = %s ORDER BY e.created_at""",
                (incident_uuid,),
            )
            events = cursor.fetchall()
            cursor.execute(
                """SELECT id, action_id, status, parameters, requested_by, reviewed_by,
                          created_at, reviewed_at FROM approvals WHERE incident_id = %s ORDER BY created_at""",
                (incident_uuid,),
            )
            approvals = cursor.fetchall()
    return {
        "id": str(incident[0]),
        "alert_name": incident[1],
        "trace_ids": incident[2],
        "summary": incident[3],
        "status": incident[4],
        "created_at": incident[5].isoformat(),
        "updated_at": incident[6].isoformat(),
        "tasks": [
            {"id": str(row[0]), "task_type": row[1], "status": row[2], "attempt": row[3], "max_attempts": row[4], "result": row[5], "error_code": row[6], "created_at": row[7].isoformat(), "updated_at": row[8].isoformat()}
            for row in tasks
        ],
        "approvals": [
            {"id": str(row[0]), "action_id": row[1], "status": row[2], "parameters": row[3], "requested_by": str(row[4]), "reviewed_by": str(row[5]) if row[5] else None, "created_at": row[6].isoformat(), "reviewed_at": row[7].isoformat() if row[7] else None}
            for row in approvals
        ],
        "timeline": [
            {"event_type": row[0], "details": row[1], "created_at": row[2].isoformat(), "actor": row[3]}
            for row in events
        ],
    }


@app.get("/api/incidents/{incident_id}/retrospective")
def get_retrospective(incident_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization)
    _authorize(principal, "incident:read", "order-service")
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT r.impact, r.root_cause, r.resolution, r.action_items, r.status,
                          r.updated_at, updated.username, r.reviewed_at, reviewed.username
                   FROM incidents i LEFT JOIN incident_retrospectives r ON r.incident_id = i.id
                   LEFT JOIN users updated ON updated.id = r.updated_by
                   LEFT JOIN users reviewed ON reviewed.id = r.reviewed_by
                   WHERE i.id = %s""",
                (incident_uuid,),
            )
            row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "impact": row[0] or "",
        "root_cause": row[1] or "",
        "resolution": row[2] or "",
        "action_items": row[3] or [],
        "status": row[4] or "draft",
        "updated_at": row[5].isoformat() if row[5] else None,
        "updated_by": row[6],
        "reviewed_at": row[7].isoformat() if row[7] else None,
        "reviewed_by": row[8],
    }


@app.put("/api/incidents/{incident_id}/retrospective")
def save_retrospective(
    incident_id: str,
    body: RetrospectiveRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    principal = _load_principal(authorization)
    _authorize(principal, "incident:review", "order-service")
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    status = "reviewed" if body.reviewed else "draft"
    with psycopg.connect(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM incidents WHERE id = %s", (incident_uuid,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=404, detail="Incident not found")
                cursor.execute(
                    """INSERT INTO incident_retrospectives
                       (incident_id, impact, root_cause, resolution, action_items, status,
                        updated_by, reviewed_by, reviewed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s,
                               CASE WHEN %s = 'reviewed' THEN %s::uuid END,
                               CASE WHEN %s = 'reviewed' THEN now() END)
                       ON CONFLICT (incident_id) DO UPDATE SET
                           impact = EXCLUDED.impact, root_cause = EXCLUDED.root_cause,
                           resolution = EXCLUDED.resolution, action_items = EXCLUDED.action_items,
                           status = EXCLUDED.status, updated_by = EXCLUDED.updated_by,
                           updated_at = now(), reviewed_by = EXCLUDED.reviewed_by,
                           reviewed_at = EXCLUDED.reviewed_at""",
                    (incident_uuid, body.impact, body.root_cause, body.resolution,
                     Jsonb(body.action_items), status, principal.user_id, status,
                     principal.user_id, status),
                )
                cursor.execute(
                    "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, %s, %s)",
                    (incident_uuid, principal.user_id, f"retrospective.{status}", Jsonb({"status": status})),
                )
    return get_retrospective(incident_uuid, authorization)


@app.post("/api/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
    principal = _load_principal(authorization)
    _authorize(principal, "task:retry", "order-service")
    try:
        task_uuid = str(uuid.UUID(task_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    with psycopg.connect(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE tasks SET status = 'queued', attempt = 0, max_attempts = 4,
                              error_code = NULL, completed_at = NULL, available_at = now(),
                              lease_expires_at = NULL, updated_at = now()
                       WHERE id = %s AND status = 'failed' RETURNING incident_id""",
                    (task_uuid,),
                )
                row = cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=409, detail="Only failed tasks can be retried")
                incident_id = row[0]
                cursor.execute(
                    "UPDATE outbox_events SET last_enqueued_at = NULL, available_at = now(), delivery_attempts = 0 WHERE payload->>'task_id' = %s",
                    (task_uuid,),
                )
                cursor.execute(
                    "INSERT INTO audit_events (incident_id, task_id, actor_id, event_type) VALUES (%s, %s, %s, 'task.manual_retry_requested')",
                    (incident_id, task_uuid, principal.user_id),
                )
    return {"task_id": task_uuid, "status": "queued"}


@app.post("/api/incidents/{incident_id}/approvals", status_code=201)
def request_approval(
    incident_id: str,
    body: ApprovalRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization)
    _authorize(principal, "task:create", "order-service")
    _require_demo_actions_enabled()
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    approval_id = str(uuid.uuid4())
    parameters = {"action": body.action, "resource": body.resource, "enabled": body.enabled}
    action_key = f"{body.action}:{'on' if body.enabled else 'off'}"
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id FROM incidents WHERE id = %s FOR UPDATE", (incident_uuid,))
                    if not cursor.fetchone():
                        raise HTTPException(status_code=404, detail="Incident not found")
                    cursor.execute(
                        """INSERT INTO approvals (id, incident_id, action_id, requested_by, parameters)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (approval_id, incident_uuid, action_key, principal.user_id, Jsonb(parameters)),
                    )
                    cursor.execute(
                        """INSERT INTO audit_events (incident_id, actor_id, event_type, details)
                           VALUES (%s, %s, 'approval.requested', %s)""",
                        (incident_uuid, principal.user_id, Jsonb({"approval_id": approval_id, **parameters})),
                    )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="An approval for this action already exists") from None
    return {"approval_id": approval_id, "status": "pending"}


@app.post("/api/approvals/{approval_id}/decision")
def decide_approval(
    approval_id: str,
    body: ApprovalDecision,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization)
    _authorize(principal, "approval:review", "order-service")
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None
    with psycopg.connect(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT incident_id, requested_by, status FROM approvals WHERE id = %s FOR UPDATE",
                    (approval_uuid,),
                )
                approval = cursor.fetchone()
                if not approval:
                    raise HTTPException(status_code=404, detail="Approval not found")
                incident_id, requested_by, status = approval
                if status != "pending":
                    raise HTTPException(status_code=409, detail="Approval is no longer pending")
                if str(requested_by) == principal.user_id:
                    raise HTTPException(status_code=403, detail="Requester cannot review their own approval")
                decision_status = "approved" if body.decision == "approve" else "rejected"
                event_type = f"approval.{decision_status}"
                cursor.execute(
                    "UPDATE approvals SET status = %s, reviewed_by = %s, reviewed_at = now() WHERE id = %s",
                    (decision_status, principal.user_id, approval_uuid),
                )
                cursor.execute(
                    """INSERT INTO audit_events (incident_id, actor_id, event_type, details)
                       VALUES (%s, %s, %s, %s)""",
                    (incident_id, principal.user_id, event_type, Jsonb({"approval_id": approval_uuid})),
                )
    return {"approval_id": approval_uuid, "status": decision_status}


@app.post("/api/approvals/{approval_id}/cancel")
def cancel_approval(approval_id: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
    principal = _load_principal(authorization)
    _authorize(principal, "task:create", "order-service")
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None
    with psycopg.connect(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT incident_id, requested_by, status FROM approvals WHERE id = %s FOR UPDATE", (approval_uuid,))
                approval = cursor.fetchone()
                if not approval:
                    raise HTTPException(status_code=404, detail="Approval not found")
                incident_id, requested_by, status = approval
                if status != "pending":
                    raise HTTPException(status_code=409, detail="Approval is no longer pending")
                if str(requested_by) != principal.user_id and principal.role != "admin":
                    raise HTTPException(status_code=403, detail="Only the requester or admin can cancel this approval")
                cursor.execute("UPDATE approvals SET status = 'cancelled' WHERE id = %s", (approval_uuid,))
                cursor.execute(
                    "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, 'approval.cancelled', %s)",
                    (incident_id, principal.user_id, Jsonb({"approval_id": approval_uuid})),
                )
    return {"approval_id": approval_uuid, "status": "cancelled"}


@app.post("/api/approvals/{approval_id}/execute")
def execute_approval(approval_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization)
    _authorize(principal, "task:create", "order-service")
    _require_demo_actions_enabled()
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None

    execution_error: str | None = None
    response: dict[str, object] | None = None
    with psycopg.connect(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT incident_id, action_id, status, requested_by, reviewed_by, parameters
                       FROM approvals WHERE id = %s FOR UPDATE""",
                    (approval_uuid,),
                )
                approval = cursor.fetchone()
                if not approval:
                    raise HTTPException(status_code=404, detail="Approval not found")
                incident_id, action_id, status, requested_by, reviewed_by, parameters = approval
                if status != "approved":
                    raise HTTPException(status_code=409, detail="Approval must be approved before execution")
                if not reviewed_by or str(reviewed_by) == str(requested_by):
                    raise HTTPException(status_code=409, detail="Approval does not have an independent reviewer")
                if not isinstance(parameters, dict) or (
                    action_id != f"set-chaos-mode:{'on' if parameters.get('enabled') else 'off'}"
                    or set(parameters) != {"action", "resource", "enabled"}
                    or parameters.get("action") != "set-chaos-mode"
                    or parameters.get("resource") != "order-service"
                    or not isinstance(parameters.get("enabled"), bool)
                ):
                    raise HTTPException(status_code=400, detail="Approval action is outside the test allowlist")

                owner = _order_action_client()
                desired = "on" if parameters["enabled"] else "off"
                before = None
                after = None
                try:
                    before = owner.state()
                    owner.set_chaos_mode(parameters["enabled"], approval_uuid)
                    after = owner.state()
                    verified = after.get("chaos_mode") == desired
                except ActionServiceError as exc:
                    try:
                        after = owner.state()
                    except ActionServiceError:
                        after = None
                    if after and after.get("chaos_mode") == desired:
                        execution_error = None
                        verified = True
                    else:
                        execution_error = exc.code
                        verified = False

                rolled_back = False
                rollback_error = None
                if execution_error is None and not verified:
                    try:
                        if not before or before.get("resource") != "order-service":
                            raise ActionServiceError("action_before_state_unavailable")
                        previous_enabled = before["chaos_mode"] == "on"
                        owner.set_chaos_mode(previous_enabled, f"{approval_uuid}:rollback")
                        rollback_state = owner.state()
                        rolled_back = rollback_state.get("chaos_mode") == before["chaos_mode"]
                        if not rolled_back:
                            rollback_error = "rollback_state_not_restored"
                    except ActionServiceError as exc:
                        rollback_error = exc.code

                if execution_error:
                    event_type = "action.failed"
                    audit_details = {"approval_id": approval_uuid, "error_code": execution_error}
                elif verified:
                    event_type = "action.verified"
                    audit_details = {"approval_id": approval_uuid, "resource": "order-service", "action": action_id, "before_chaos_mode": before.get("chaos_mode") if before else None, "chaos_mode": desired}
                    cursor.execute("UPDATE approvals SET status = 'executed' WHERE id = %s", (approval_uuid,))
                else:
                    event_type = "action.rollback_completed" if rolled_back else "action.rollback_failed"
                    audit_details = {"approval_id": approval_uuid, "resource": "order-service", "action": action_id, "before_chaos_mode": before.get("chaos_mode") if before else None, "rolled_back": rolled_back, "error_code": rollback_error or "verification_failed"}
                    cursor.execute("UPDATE approvals SET status = 'executed' WHERE id = %s", (approval_uuid,))
                cursor.execute(
                    "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, %s, %s)",
                    (incident_id, principal.user_id, event_type, Jsonb(audit_details)),
                )
                if execution_error is None and verified:
                    response = {"approval_id": approval_uuid, "status": "executed", "verified": True, "chaos_mode": desired}
                elif execution_error is None:
                    response = {"approval_id": approval_uuid, "status": "executed", "verified": False, "rolled_back": rolled_back}

    if execution_error:
        raise HTTPException(status_code=502, detail="Demo action owner could not confirm execution")
    if response and response["verified"] is False:
        message = "Action verification failed and the original state was restored" if response["rolled_back"] else "Action verification failed and rollback could not be confirmed"
        raise HTTPException(status_code=502, detail=message)
    return response or {"approval_id": approval_uuid, "status": "executed", "verified": False}


@app.post("/webhooks/openobserve", status_code=202)
@app.post("/", status_code=202, include_in_schema=False)
async def openobserve_webhook(request: Request) -> dict[str, object]:
    configured = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    supplied = request.headers.get("X-Alert-Token", "")
    if not configured or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    if content_length <= 0 or content_length > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large or empty")
    try:
        name, traces, summary, fingerprint = normalize_alert(await request.body())
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid alert payload") from exc

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise HTTPException(status_code=503, detail="Incident database is not configured")
    alert = IncidentInput(fingerprint, name, tuple(traces), summary)
    with psycopg.connect(database_url) as conn:
        try:
            result = create_incident(conn, alert)
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail="Incident could not be persisted") from exc
    return {
        "accepted": True,
        "duplicate": not result["created"],
        "incident_id": result["incident_id"],
        "task_id": result["task_id"],
        "trace_ids": traces,
    }


app.mount("/", StaticFiles(directory=str(PUBLIC_PATH), html=True), name="workbench")
