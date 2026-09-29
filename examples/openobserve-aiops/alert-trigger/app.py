"""Authenticated webhook API and local database migration lifecycle."""

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path

import psycopg
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb

from action_client import ActionServiceError, OrderActionClient
from auth import ROLE_PERMISSIONS, authorize, create_session, hash_password, parse_session, require_permission, session_expiration, verify_password
from db_config import connect_database, validate_database_url
from models import IncidentInput, Principal
from store import IncidentResourceConflict, TaskQueueAtCapacity, create_incident
from trigger import DEFAULT_RESOURCE, MAX_BODY_BYTES, normalize_alert, normalize_alertmanager_payload, normalize_resource
from worker import start_outbox_dispatcher
from identity import OIDCSettings, load_oidc_settings, principal_claims

PUBLIC_PATH = Path(__file__).parent / "public"
MIGRATIONS_PATH = Path(__file__).parent / "migrations"
REQUIRED_MIGRATIONS = frozenset(path.stem for path in MIGRATIONS_PATH.glob("*.sql"))
SESSION_COOKIE_NAME = "aiops_session"
OIDC_STATE_COOKIE_NAME = "aiops_oidc_state"
OIDC_SESSION_TTL_SECONDS = 900
OIDC_LOGIN_ADMISSION_LOCK = 82476219
USER_LIFECYCLE_LOCK = 731905241
ACTION_OWNER_RESOURCE_LOCK = "holmes-aiops:action-owner:order-service"
API_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
API_METRICS_LOCK = threading.Lock()
API_REQUESTS_IN_FLIGHT = 0
API_REQUEST_METRICS: dict[tuple[str, str, str], dict[str, object]] = {}
OIDC_LOGIN_ADMISSION_REJECTIONS_TOTAL = 0


def _auth_mode() -> str:
    runtime = os.getenv("AIOPS_ENV", "production")
    default = "local" if runtime == "local" else "oidc"
    mode = os.getenv("AIOPS_AUTH_MODE", default)
    if mode not in {"local", "oidc"} or (mode == "local" and runtime != "local"):
        raise RuntimeError("Local password authentication is only permitted when AIOPS_ENV=local")
    return mode


def _oidc_settings() -> OIDCSettings:
    try:
        return load_oidc_settings(dict(os.environ))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None


def _oidc_client(settings: OIDCSettings | None = None):
    settings = settings or _oidc_settings()
    registry = OAuth()
    registry.register(
        name="workforce",
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        server_metadata_url=settings.metadata_url,
        client_kwargs={"scope": " ".join(settings.scopes)},
    )
    return registry.create_client("workforce")


async def _exchange_oidc_code(settings: OIDCSettings, metadata: dict[str, object], code: str, verifier: str):
    async with AsyncOAuth2Client(settings.client_id, settings.client_secret) as client:
        return await client.fetch_token(
            str(metadata["token_endpoint"]),
            grant_type="authorization_code",
            code=code,
            redirect_uri=settings.redirect_uri,
            code_verifier=verifier,
        )


def _validate_auth_configuration() -> OIDCSettings | None:
    mode = _auth_mode()
    raw_users = _test_users_configuration()
    if mode == "local":
        return None
    if raw_users:
        raise RuntimeError("AIOPS_TEST_USERS_JSON cannot be used with OIDC authentication")
    if len(os.getenv("SESSION_SIGNING_KEY", "")) < 32:
        raise RuntimeError("SESSION_SIGNING_KEY must contain at least 32 bytes for OIDC authentication")
    if os.getenv("AIOPS_ENV", "production") != "local" and os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "true":
        raise RuntimeError("SESSION_COOKIE_SECURE must be true for OIDC authentication")
    _max_pending_oidc_logins()
    return _oidc_settings()


def _validate_metrics_configuration() -> None:
    if os.getenv("AIOPS_ENV", "production") != "local" and len(os.getenv("AIOPS_METRICS_TOKEN", "")) < 32:
        raise RuntimeError("AIOPS_METRICS_TOKEN must contain at least 32 bytes outside local mode")


def _validate_webhook_configuration() -> None:
    if os.getenv("AIOPS_ENV", "production") == "local":
        return
    alert_token = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    if len(alert_token.encode("utf-8")) < 32:
        raise RuntimeError("ALERT_WEBHOOK_TOKEN must contain at least 32 bytes outside local mode")
    for name in (
        "ALERT_WEBHOOK_TOKEN_PREVIOUS",
        "ALERTMANAGER_WEBHOOK_TOKEN",
        "ALERTMANAGER_WEBHOOK_TOKEN_PREVIOUS",
    ):
        token = os.getenv(name, "")
        if token and len(token.encode("utf-8")) < 32:
            raise RuntimeError(f"{name} must contain at least 32 bytes when configured")


def _matches_webhook_token(supplied: str, *configured_tokens: str) -> bool:
    supplied_bytes = supplied.encode("utf-8")
    matched = False
    for token in configured_tokens:
        if token:
            matched |= hmac.compare_digest(supplied_bytes, token.encode("utf-8"))
    return matched


def _max_pending_tasks() -> int | None:
    raw = os.getenv("AIOPS_MAX_PENDING_TASKS", "").strip()
    if not raw and os.getenv("AIOPS_ENV", "production") == "local":
        return None
    try:
        capacity = int(raw)
    except ValueError:
        raise RuntimeError("AIOPS_MAX_PENDING_TASKS must be a positive integer outside local mode") from None
    if capacity < 1:
        raise RuntimeError("AIOPS_MAX_PENDING_TASKS must be a positive integer")
    return capacity


def _max_pending_oidc_logins() -> int:
    raw = os.getenv("AIOPS_OIDC_MAX_PENDING_LOGINS", "").strip()
    if not raw and os.getenv("AIOPS_ENV", "production") == "local":
        return 500
    try:
        capacity = int(raw)
    except ValueError:
        raise RuntimeError("AIOPS_OIDC_MAX_PENDING_LOGINS must be a positive integer outside local mode") from None
    if capacity < 1:
        raise RuntimeError("AIOPS_OIDC_MAX_PENDING_LOGINS must be a positive integer")
    return capacity


def _oldest_pending_task_age_slo_seconds() -> float | None:
    raw = os.getenv("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS", "").strip()
    if not raw and os.getenv("AIOPS_ENV", "production") == "local":
        return None
    try:
        threshold = float(raw)
    except ValueError:
        raise RuntimeError(
            "AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS must be a positive number outside local mode"
        ) from None
    if not math.isfinite(threshold) or threshold <= 0:
        raise RuntimeError("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS must be a positive number")
    return threshold


def _outbox_unpublished_age_slo_seconds() -> float | None:
    raw = os.getenv("AIOPS_OUTBOX_UNPUBLISHED_AGE_SLO_SECONDS", "").strip()
    if not raw and os.getenv("AIOPS_ENV", "production") == "local":
        return None
    try:
        threshold = float(raw)
    except ValueError:
        raise RuntimeError(
            "AIOPS_OUTBOX_UNPUBLISHED_AGE_SLO_SECONDS must be a positive number outside local mode"
        ) from None
    if not math.isfinite(threshold) or threshold <= 0:
        raise RuntimeError("AIOPS_OUTBOX_UNPUBLISHED_AGE_SLO_SECONDS must be a positive number")
    return threshold


def _action_execution_recovery_age_slo_seconds() -> float | None:
    raw = os.getenv("AIOPS_ACTION_EXECUTION_RECOVERY_AGE_SLO_SECONDS", "").strip()
    if not raw and os.getenv("AIOPS_ENV", "production") == "local":
        return None
    try:
        threshold = float(raw)
    except ValueError:
        raise RuntimeError(
            "AIOPS_ACTION_EXECUTION_RECOVERY_AGE_SLO_SECONDS must be a positive number outside local mode"
        ) from None
    if not math.isfinite(threshold) or threshold <= 0:
        raise RuntimeError("AIOPS_ACTION_EXECUTION_RECOVERY_AGE_SLO_SECONDS must be a positive number")
    return threshold


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_possible_duplicate_charge: bool = False


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


class IncidentTriageUpdate(BaseModel):
    severity: str | None = Field(default=None, pattern="^(critical|high|medium|low)$")
    assignee_id: str | None = Field(default=None, max_length=36)

    model_config = {"extra": "forbid"}


class IncidentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(open|investigating|awaiting_approval|resolved|closed)$")

    model_config = {"extra": "forbid"}


INCIDENT_STATUS_TRANSITIONS = {
    "open": frozenset({"investigating", "closed"}),
    "investigating": frozenset({"awaiting_approval", "resolved", "closed"}),
    "awaiting_approval": frozenset({"investigating", "resolved", "closed"}),
    "resolved": frozenset({"investigating", "closed"}),
    "closed": frozenset(),
}


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
        prepared.append((user_id, username, hash_password(password), role, sorted({scope.lower() for scope in scopes})))
    with connect_database(database_url) as conn:
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
    try:
        return validate_database_url(value, "DATABASE_URL", required=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _test_users_configuration() -> str:
    raw_users = os.getenv("AIOPS_TEST_USERS_JSON", "")
    if raw_users and os.getenv("AIOPS_ENV") != "local":
        raise RuntimeError("AIOPS_TEST_USERS_JSON is only permitted when AIOPS_ENV=local")
    return raw_users


def _load_principal(
    authorization: str | None,
    session_token: str | None = None,
    *,
    cursor=None,
    authorization_locks_held: bool = False,
) -> Principal:
    token = session_token
    oidc_mode = _auth_mode() == "oidc"
    if oidc_mode and not session_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not oidc_mode and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    key = os.getenv("SESSION_SIGNING_KEY", "")
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="Session authentication is not configured")
    try:
        token_principal = parse_session(token, key)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    query = """SELECT id, username, role, resource_scopes, session_generation FROM users
               WHERE id = %s::uuid AND active = TRUE AND session_generation = %s
                 AND NOT EXISTS (
                     SELECT 1 FROM revoked_sessions
                     WHERE token_hash = %s AND expires_at > now()
                 )"""
    params = (token_principal.user_id, token_principal.session_generation, hashlib.sha256(token.encode()).hexdigest())
    if cursor is not None:
        if not authorization_locks_held:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (params[2],))
        cursor.execute(f"{query} FOR UPDATE", params)
        user = cursor.fetchone()
    else:
        with connect_database(_database_url()) as conn:
            with conn.cursor() as db_cursor:
                db_cursor.execute(query, params)
                user = db_cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return Principal(str(user[0]), user[1], user[2], tuple(scope.lower() for scope in user[3]), int(user[4]))


def _authorize(principal, permission: str, resource: str) -> None:
    try:
        require_permission(principal, permission, resource)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden") from None


def _require_role_permission(principal: Principal, permission: str) -> None:
    if permission not in ROLE_PERMISSIONS.get(principal.role, frozenset()):
        raise HTTPException(status_code=403, detail="Forbidden")


def _authorize_incident_resource(principal: Principal, permission: str, resource: str) -> None:
    if not authorize(principal, permission, resource):
        raise HTTPException(status_code=404, detail="Incident not found")


def _order_action_client() -> OrderActionClient:
    try:
        return OrderActionClient(
            os.getenv("ORDER_SERVICE_URL", "http://order-service:8080"),
            os.getenv("ORDER_ACTION_SERVICE_TOKEN", ""),
        )
    except ValueError:
        raise HTTPException(status_code=503, detail="Demo action service is not configured") from None


def _require_demo_actions_enabled() -> None:
    if (
        os.getenv("AIOPS_ENV", "production") != "local"
        or os.getenv("AIOPS_DEMO_ACTIONS_ENABLED", "").lower() != "true"
    ):
        raise HTTPException(status_code=503, detail="Demo remediation actions are disabled")


def _validate_demo_action_configuration() -> None:
    if os.getenv("AIOPS_ENV", "production") == "local":
        return
    demo_action_variables = (
        "AIOPS_DEMO_ACTIONS_ENABLED",
        "ORDER_SERVICE_URL",
        "ORDER_ACTION_SERVICE_TOKEN",
    )
    if any(os.getenv(name, "").strip() for name in demo_action_variables):
        raise RuntimeError("Demo remediation settings are only permitted when AIOPS_ENV=local")


def _reconcile_action_executions_once(database_url: str) -> None:
    if os.getenv("AIOPS_ENV", "production") != "local" or os.getenv("AIOPS_DEMO_ACTIONS_ENABLED", "").lower() != "true":
        return

    with connect_database(database_url, autocommit=True) as lock_conn:
        with lock_conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (ACTION_OWNER_RESOURCE_LOCK,))
            if not cursor.fetchone()[0]:
                return
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (USER_LIFECYCLE_LOCK,))
            lifecycle_lock_acquired = cursor.fetchone()[0]
        if not lifecycle_lock_acquired:
            lock_conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (ACTION_OWNER_RESOURCE_LOCK,))
            return
        try:
            with connect_database(database_url) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """SELECT e.approval_id, e.incident_id, e.status, e.before_state,
                                  a.requested_by, a.reviewed_by, a.action_id, a.parameters
                           FROM action_executions e JOIN approvals a ON a.id = e.approval_id
                           JOIN incidents i ON i.id = e.incident_id
                           WHERE e.status IN ('dispatching', 'rollback_pending')
                             AND e.updated_at < now() - interval '5 seconds'
                             AND a.status = 'approved' AND i.resource = 'order-service'
                           ORDER BY e.updated_at, e.approval_id LIMIT 1"""
                    )
                    pending = cursor.fetchone()
            if not pending:
                return

            owner = _order_action_client()
            for approval_id, incident_id, execution_status, before_state, requested_by, reviewed_by, action_id, parameters in (pending,):
                if (
                    not reviewed_by or reviewed_by == requested_by or not isinstance(parameters, dict)
                    or parameters.get("action") != "set-chaos-mode"
                    or parameters.get("resource") != "order-service"
                    or not isinstance(parameters.get("enabled"), bool)
                    or set(parameters) != {"action", "resource", "enabled"}
                    or action_id != f"set-chaos-mode:{'on' if parameters.get('enabled') else 'off'}"
                ):
                    continue
                is_rollback = execution_status == "rollback_pending"
                if is_rollback and (
                    not isinstance(before_state, dict)
                    or before_state.get("resource") != "order-service"
                    or before_state.get("chaos_mode") not in {"on", "off"}
                ):
                    continue
                expected_enabled = before_state["chaos_mode"] == "on" if is_rollback else parameters["enabled"]
                operation_id = f"{approval_id}:rollback" if is_rollback else str(approval_id)
                try:
                    if is_rollback and owner.operation_result(str(approval_id), parameters["enabled"]) is None:
                        continue
                    operation = owner.operation_result(operation_id, expected_enabled)
                    if operation is None:
                        continue
                    desired = "on" if expected_enabled else "off"
                    if owner.state().get("chaos_mode") != desired:
                        continue
                except ActionServiceError:
                    continue

                with connect_database(database_url) as conn:
                    with conn.transaction():
                        with conn.cursor() as cursor:
                            cursor.execute(
                                """SELECT e.status, a.status, a.requested_by, a.reviewed_by, a.action_id, a.parameters, e.before_state
                                   FROM action_executions e JOIN approvals a ON a.id = e.approval_id
                                   JOIN incidents i ON i.id = e.incident_id
                                   WHERE e.approval_id = %s AND e.incident_id = %s AND i.resource = 'order-service'
                                   FOR UPDATE OF e, a, i""",
                                (approval_id, incident_id),
                            )
                            current = cursor.fetchone()
                            expected_status = "rollback_pending" if is_rollback else "dispatching"
                            if (
                                not current or current[0] != expected_status or current[1] != "approved"
                                or current[2] != requested_by or current[3] != reviewed_by
                                or current[4] != action_id or current[5] != parameters
                                or current[6] != before_state
                            ):
                                continue
                            cursor.execute(
                                "UPDATE action_executions SET status = %s, error_code = %s, updated_at = now() WHERE approval_id = %s AND status = %s",
                                ("rolled_back" if is_rollback else "succeeded", "postcondition_failed" if is_rollback else None, approval_id, execution_status),
                            )
                            if cursor.rowcount != 1:
                                continue
                            if not is_rollback:
                                cursor.execute("UPDATE approvals SET status = 'executed' WHERE id = %s AND status = 'approved'", (approval_id,))
                            cursor.execute(
                                "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, NULL, %s, %s)",
                                (incident_id, "action.rollback_reconciled" if is_rollback else "action.reconciled", Jsonb({"approval_id": str(approval_id), "resource": "order-service", "action": action_id, "source": "owner_idempotency_journal"})),
                            )
        finally:
            with lock_conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (USER_LIFECYCLE_LOCK,))
                cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (ACTION_OWNER_RESOURCE_LOCK,))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_demo_action_configuration()
    _validate_metrics_configuration()
    _validate_webhook_configuration()
    _max_pending_tasks()
    _oldest_pending_task_age_slo_seconds()
    _outbox_unpublished_age_slo_seconds()
    settings = _validate_auth_configuration()
    if settings:
        metadata = await _oidc_client(settings).load_server_metadata()
        if metadata.get("issuer") != settings.issuer or "S256" not in metadata.get("code_challenge_methods_supported", []):
            raise RuntimeError("OIDC discovery issuer or PKCE S256 capability is invalid")
    database_url = validate_database_url(
        os.getenv("DATABASE_URL", ""),
        "DATABASE_URL",
        required=os.getenv("AIOPS_ENV", "production") != "local",
    )
    if database_url:
        raw_users = _test_users_configuration()
        if raw_users:
            _seed_test_users(database_url, raw_users)
        maintenance_callback = _reconcile_action_executions_once if os.getenv("AIOPS_DEMO_ACTIONS_ENABLED", "").lower() == "true" else None
        dispatcher = start_outbox_dispatcher(database_url, maintenance_callback)
        try:
            yield
        finally:
            dispatcher.stop()
    else:
        yield


app = FastAPI(title="Holmes AIOps Incident Service", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def protect_cookie_authenticated_mutations(request: Request, call_next):
    global API_REQUESTS_IN_FLIGHT
    measure_request = request.url.path != "/_internal/metrics"
    if measure_request:
        with API_METRICS_LOCK:
            API_REQUESTS_IN_FLIGHT += 1
    started = time.perf_counter()
    status_code = 500
    unsafe_method = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    cookie_session = request.cookies.get(SESSION_COOKIE_NAME)
    bearer_auth = request.headers.get("authorization", "").startswith("Bearer ")
    try:
        if unsafe_method and cookie_session and (_auth_mode() == "oidc" or not bearer_auth):
            expected_origin = os.getenv("AIOPS_PUBLIC_ORIGIN", "").rstrip("/")
            origin = request.headers.get("origin", "").rstrip("/")
            if not expected_origin or not origin or not hmac.compare_digest(origin, expected_origin):
                response = JSONResponse({"detail": "Request origin is not allowed"}, status_code=403)
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        # Keep common browser-side protections on every response handled here.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith(("/api/", "/auth/", "/_internal/")):
            response.headers["Cache-Control"] = "no-store"
        status_code = response.status_code
        return response
    finally:
        if measure_request:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            route_name = getattr(route, "path", "unmatched")
            label = (request.method, route_name, f"{status_code // 100}xx")
            with API_METRICS_LOCK:
                API_REQUESTS_IN_FLIGHT -= 1
                metric = API_REQUEST_METRICS.setdefault(
                    label,
                    {"count": 0, "duration_sum": 0.0, "buckets": [0] * (len(API_DURATION_BUCKETS) + 1)},
                )
                metric["count"] = int(metric["count"]) + 1
                metric["duration_sum"] = float(metric["duration_sum"]) + elapsed
                buckets = metric["buckets"]
                for index, boundary in enumerate(API_DURATION_BUCKETS):
                    if elapsed <= boundary:
                        buckets[index] += 1
                buckets[-1] += 1


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    database_url = _database_url()
    try:
        with connect_database(database_url, connect_timeout=3) as conn:
            applied_migrations = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail="Incident database schema is not current") from None
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Incident database is unavailable") from None
    if not REQUIRED_MIGRATIONS.issubset(applied_migrations):
        raise HTTPException(status_code=503, detail="Incident database schema is not current")
    return {"status": "ready"}


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _decode_urlsafe_cursor(cursor: str) -> bytes:
    decoded = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != cursor:
        raise ValueError("Cursor encoding is not canonical")
    return decoded


def _decode_incident_cursor(cursor: str) -> tuple[str, str]:
    try:
        decoded = _decode_urlsafe_cursor(cursor)
        value = json.loads(decoded)
        created_at = value["created_at"]
        incident_id = str(uuid.UUID(value["id"]))
        timestamp = datetime.fromisoformat(created_at)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Incident cursor timestamp must include a timezone")
        return created_at, incident_id
    except (binascii.Error, ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid incident cursor") from None


def _encode_incident_cursor(created_at: str, incident_id: str) -> str:
    value = json.dumps({"created_at": created_at, "id": incident_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_timeline_cursor(cursor: str) -> tuple[str, int]:
    try:
        decoded = _decode_urlsafe_cursor(cursor)
        value = json.loads(decoded)
        created_at = value["created_at"]
        timestamp = datetime.fromisoformat(created_at)
        event_id = value["id"]
        if (
            timestamp.tzinfo is None
            or timestamp.utcoffset() is None
            or isinstance(event_id, bool)
            or not isinstance(event_id, int)
            or not 1 <= event_id <= 9_223_372_036_854_775_807
        ):
            raise ValueError("Invalid timeline cursor")
        return created_at, event_id
    except (binascii.Error, ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid timeline cursor") from None


def _encode_timeline_cursor(created_at: str, event_id: int) -> str:
    value = json.dumps({"created_at": created_at, "id": event_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _record_oidc_admission_rejection() -> None:
    global OIDC_LOGIN_ADMISSION_REJECTIONS_TOTAL
    with API_METRICS_LOCK:
        OIDC_LOGIN_ADMISSION_REJECTIONS_TOTAL += 1


async def _read_bounded_webhook_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_length > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")
        body.extend(chunk)
    if not body:
        raise HTTPException(status_code=400, detail="Webhook payload is empty")
    return bytes(body)


@app.get("/_internal/metrics", include_in_schema=False)
def internal_metrics(request: Request) -> PlainTextResponse:
    configured = os.getenv("AIOPS_METRICS_TOKEN", "")
    if not configured:
        raise HTTPException(status_code=404, detail="Metrics are disabled")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, f"Bearer {configured}"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    database_url = _database_url()
    try:
        with connect_database(database_url, connect_timeout=3) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT status, count(*), COALESCE(sum(GREATEST(attempt - 1, 0)), 0)
                       FROM tasks GROUP BY status"""
                )
                task_metrics = cursor.fetchall()
                status_counts = {status: count for status, count, _ in task_metrics}
                retry_attempts = sum(retries for _, _, retries in task_metrics)
                cursor.execute(
                    """SELECT count(*),
                              COALESCE(percentile_cont(0.50) WITHIN GROUP (
                                  ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))), 0),
                              COALESCE(percentile_cont(0.95) WITHIN GROUP (
                                  ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))), 0)
                       FROM tasks
                       WHERE status IN ('completed', 'failed')
                         AND started_at IS NOT NULL AND completed_at IS NOT NULL
                         AND completed_at >= now() - interval '24 hours'
                         AND completed_at >= started_at"""
                )
                task_duration_count, task_duration_p50, task_duration_p95 = cursor.fetchone()
                cursor.execute(
                    """SELECT COALESCE(EXTRACT(EPOCH FROM (now() - min(created_at))), 0)
                       FROM tasks WHERE status IN ('queued', 'running', 'retrying')"""
                )
                oldest_pending_age = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM oidc_login_transactions WHERE expires_at > now()")
                pending_oidc_logins = cursor.fetchone()[0]
                cursor.execute(
                    """WITH eligible_tasks AS MATERIALIZED (
                           SELECT id
                           FROM tasks
                           WHERE status IN ('queued', 'retrying')
                             AND available_at <= now()
                       ), eligible_outbox AS MATERIALIZED (
                           SELECT o.published_at, o.created_at, o.delivery_attempts
                           FROM eligible_tasks t
                           JOIN LATERAL (
                               SELECT published_at, created_at, delivery_attempts
                               FROM outbox_events
                               WHERE event_type = 'investigation.requested'
                                 AND payload->>'task_id' = t.id::text
                                 AND available_at <= now()
                               OFFSET 0
                           ) o ON true
                       )
                       SELECT count(*) FILTER (WHERE o.published_at IS NULL),
                              COALESCE(EXTRACT(EPOCH FROM (
                                  now() - min(o.created_at) FILTER (WHERE o.published_at IS NULL)
                              )), 0),
                              COALESCE(max(o.delivery_attempts) FILTER (WHERE o.published_at IS NULL), 0),
                              COALESCE(max(o.delivery_attempts), 0)
                       FROM eligible_outbox o"""
                )
                (
                    unpublished_outbox_events,
                    oldest_unpublished_outbox_age,
                    max_unpublished_outbox_delivery_attempts,
                    max_outbox_delivery_attempts,
                ) = cursor.fetchone()
                cursor.execute(
                    """SELECT COALESCE(EXTRACT(EPOCH FROM (now() - min(updated_at))), 0)
                       FROM action_executions WHERE status IN ('dispatching', 'rollback_pending')"""
                )
                oldest_action_execution_recovery_age = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM outbox_events WHERE dead_lettered_at IS NOT NULL")
                outbox_dead_lettered_events = cursor.fetchone()[0]
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Incident metrics are temporarily unavailable") from None

    with API_METRICS_LOCK:
        in_flight = API_REQUESTS_IN_FLIGHT
        oidc_admission_rejections = OIDC_LOGIN_ADMISSION_REJECTIONS_TOTAL
        api_metrics = [
            (
                method,
                route,
                status_class,
                int(metric["count"]),
                float(metric["duration_sum"]),
                list(metric["buckets"]),
            )
            for (method, route, status_class), metric in API_REQUEST_METRICS.items()
        ]

    statuses = ("queued", "running", "retrying", "completed", "failed", "cancelled")
    lines = [
        "# HELP aiops_tasks Current incident tasks grouped by lifecycle status.",
        "# TYPE aiops_tasks gauge",
        *(f'aiops_tasks{{status="{status}"}} {int(status_counts.get(status, 0))}' for status in statuses),
        "# HELP aiops_oldest_pending_task_age_seconds Age of the oldest queued, running, or retrying task.",
        "# TYPE aiops_oldest_pending_task_age_seconds gauge",
        f"aiops_oldest_pending_task_age_seconds {max(0, float(oldest_pending_age or 0))}",
        "# HELP aiops_task_retry_attempts_total Persisted task retry attempts beyond the initial attempt.",
        "# TYPE aiops_task_retry_attempts_total counter",
        f"aiops_task_retry_attempts_total {int(retry_attempts or 0)}",
        "# HELP aiops_oidc_pending_login_transactions Unexpired OIDC login transactions reserved across API replicas.",
        "# TYPE aiops_oidc_pending_login_transactions gauge",
        f"aiops_oidc_pending_login_transactions {int(pending_oidc_logins or 0)}",
        "# HELP aiops_oidc_login_admission_rejections_total OIDC login starts rejected because the shared pending transaction cap was reached; process-local counter.",
        "# TYPE aiops_oidc_login_admission_rejections_total counter",
        f"aiops_oidc_login_admission_rejections_total {oidc_admission_rejections}",
        "# HELP aiops_outbox_unpublished_events Outbox events available for dispatch that have not yet been accepted by the broker.",
        "# TYPE aiops_outbox_unpublished_events gauge",
        f"aiops_outbox_unpublished_events {int(unpublished_outbox_events or 0)}",
        "# HELP aiops_outbox_oldest_unpublished_age_seconds Age of the oldest available outbox event not yet accepted by the broker.",
        "# TYPE aiops_outbox_oldest_unpublished_age_seconds gauge",
        f"aiops_outbox_oldest_unpublished_age_seconds {max(0, float(oldest_unpublished_outbox_age or 0))}",
        "# HELP aiops_outbox_max_unpublished_delivery_attempts Highest reserved broker publish attempt among available queued or retrying investigation events not yet accepted by the broker.",
        "# TYPE aiops_outbox_max_unpublished_delivery_attempts gauge",
        f"aiops_outbox_max_unpublished_delivery_attempts {int(max_unpublished_outbox_delivery_attempts or 0)}",
        "# HELP aiops_action_execution_oldest_recovery_age_seconds Age of the oldest action execution awaiting operator reconciliation.",
        "# TYPE aiops_action_execution_oldest_recovery_age_seconds gauge",
        f"aiops_action_execution_oldest_recovery_age_seconds {max(0, float(oldest_action_execution_recovery_age or 0))}",
        "# HELP aiops_outbox_max_delivery_attempts Highest reserved broker publish attempt among queued or retrying investigation events, including events waiting for backoff or worker claim.",
        "# TYPE aiops_outbox_max_delivery_attempts gauge",
        f"aiops_outbox_max_delivery_attempts {int(max_outbox_delivery_attempts or 0)}",
        "# HELP aiops_outbox_dead_lettered_events Investigation outbox events stopped after the configured broker delivery limit.",
        "# TYPE aiops_outbox_dead_lettered_events gauge",
        f"aiops_outbox_dead_lettered_events {int(outbox_dead_lettered_events or 0)}",
        "# HELP aiops_worker_task_duration_seconds_windowed Recent worker task execution duration percentiles over a rolling 24 hour window.",
        "# TYPE aiops_worker_task_duration_seconds_windowed gauge",
        f'aiops_worker_task_duration_seconds_windowed{{quantile="0.50"}} {float(task_duration_p50 or 0)}',
        f'aiops_worker_task_duration_seconds_windowed{{quantile="0.95"}} {float(task_duration_p95 or 0)}',
        "# HELP aiops_worker_task_duration_samples_windowed Number of completed or failed worker tasks in the rolling 24 hour duration sample.",
        "# TYPE aiops_worker_task_duration_samples_windowed gauge",
        f"aiops_worker_task_duration_samples_windowed {int(task_duration_count or 0)}",
        "# HELP aiops_api_requests_in_flight API requests currently executing in this process.",
        "# TYPE aiops_api_requests_in_flight gauge",
        f"aiops_api_requests_in_flight {in_flight}",
        "# HELP aiops_api_requests_total Completed API requests since process start, excluding this metrics endpoint.",
        "# TYPE aiops_api_requests_total counter",
        "# HELP aiops_api_request_duration_seconds API request duration since process start, excluding this metrics endpoint.",
        "# TYPE aiops_api_request_duration_seconds histogram",
    ]
    for method, route, status_class, count, duration_sum, buckets in api_metrics:
        labels = (
            f'method="{_prometheus_label(method)}",'
            f'route="{_prometheus_label(route)}",'
            f'status_class="{_prometheus_label(status_class)}"'
        )
        for index, boundary in enumerate(API_DURATION_BUCKETS):
            lines.append(f'aiops_api_request_duration_seconds_bucket{{{labels},le="{boundary}"}} {buckets[index]}')
        lines.extend(
            [
                f'aiops_api_request_duration_seconds_bucket{{{labels},le="+Inf"}} {buckets[-1]}',
                f"aiops_api_request_duration_seconds_sum{{{labels}}} {duration_sum}",
                f"aiops_api_request_duration_seconds_count{{{labels}}} {count}",
                f"aiops_api_requests_total{{{labels}}} {count}",
            ]
        )
    capacity = _max_pending_tasks()
    if capacity is not None:
        lines.extend([
            "# HELP aiops_pending_task_capacity Maximum admitted queued, running, and retrying tasks.",
            "# TYPE aiops_pending_task_capacity gauge",
            f"aiops_pending_task_capacity {capacity}",
        ])
    pending_task_age_slo = _oldest_pending_task_age_slo_seconds()
    if pending_task_age_slo is not None:
        lines.extend([
            "# HELP aiops_oldest_pending_task_age_slo_seconds Owner-configured maximum age for queued, running, or retrying tasks.",
            "# TYPE aiops_oldest_pending_task_age_slo_seconds gauge",
            f"aiops_oldest_pending_task_age_slo_seconds {pending_task_age_slo}",
        ])
    outbox_age_slo = _outbox_unpublished_age_slo_seconds()
    if outbox_age_slo is not None:
        lines.extend([
            "# HELP aiops_outbox_unpublished_age_slo_seconds Owner-configured maximum age for outbox events awaiting broker acceptance.",
            "# TYPE aiops_outbox_unpublished_age_slo_seconds gauge",
            f"aiops_outbox_unpublished_age_slo_seconds {outbox_age_slo}",
        ])
    action_execution_recovery_age_slo = _action_execution_recovery_age_slo_seconds()
    if action_execution_recovery_age_slo is not None:
        lines.extend([
            "# HELP aiops_action_execution_recovery_age_slo_seconds Owner-configured maximum age for operator reconciliation of interrupted actions.",
            "# TYPE aiops_action_execution_recovery_age_slo_seconds gauge",
            f"aiops_action_execution_recovery_age_slo_seconds {action_execution_recovery_age_slo}",
        ])
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/", include_in_schema=False)
def workbench() -> FileResponse:
    return FileResponse(PUBLIC_PATH / "incidents.html")


@app.get("/auth/mode")
def auth_mode() -> dict[str, str]:
    return {"mode": _auth_mode()}


@app.get("/auth/local-test-defaults")
def local_test_login_defaults():
    if _auth_mode() != "local":
        raise HTTPException(status_code=404, detail="Not found")
    users = json.loads(_test_users_configuration() or "[]")
    accounts = [
        {"username": user["username"], "password": user["password"], "role": user["role"]}
        for user in users
        if isinstance(user, dict)
        and isinstance(user.get("username"), str)
        and isinstance(user.get("password"), str)
        and isinstance(user.get("role"), str)
    ]
    if not accounts:
        raise HTTPException(status_code=404, detail="Local test accounts are not configured")
    operator = next((account for account in accounts if account["role"] == "operator"), None)
    default_username = operator["username"] if operator else accounts[0]["username"]
    return JSONResponse(
        {"default_username": default_username, "accounts": accounts},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/auth/login")
async def oidc_login(request: Request):
    if _auth_mode() != "oidc":
        raise HTTPException(status_code=404, detail="OIDC login is not enabled")
    settings = _oidc_settings()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    capacity_reached = False
    retry_after = 1
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (OIDC_LOGIN_ADMISSION_LOCK,))
            cursor.execute("DELETE FROM oidc_login_transactions WHERE expires_at <= now()")
            cursor.execute(
                """SELECT count(*), GREATEST(1, COALESCE(ceil(extract(epoch FROM (min(expires_at) - now())))::integer, 1))
                   FROM oidc_login_transactions"""
            )
            pending_count, retry_after = cursor.fetchone()
            if pending_count >= _max_pending_oidc_logins():
                capacity_reached = True
            else:
                cursor.execute(
                    "INSERT INTO oidc_login_transactions (state_hash, code_verifier, nonce, expires_at) VALUES (%s, %s, %s, now() + interval '10 minutes')",
                    (state_hash, verifier, nonce),
                )
    if capacity_reached:
        _record_oidc_admission_rejection()
        raise HTTPException(
            status_code=503,
            detail="OIDC login capacity is temporarily full",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        client = _oidc_client(settings)
        await client.load_server_metadata()
        authorization = await client.create_authorization_url(
            redirect_uri=settings.redirect_uri,
            scope=" ".join(settings.scopes),
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            code_challenge_method="S256",
        )
    except Exception:
        with connect_database(_database_url()) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM oidc_login_transactions WHERE state_hash = %s", (state_hash,))
        raise HTTPException(status_code=503, detail="OIDC login provider is temporarily unavailable") from None
    response = RedirectResponse(authorization["url"], status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE_NAME,
        state,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "true" if os.getenv("AIOPS_ENV", "production") != "local" else "false").lower() == "true",
        samesite="lax",
        max_age=600,
        path="/auth/oidc/callback",
    )
    return response


@app.get("/auth/oidc/callback")
async def oidc_callback(request: Request):
    if _auth_mode() != "oidc":
        raise HTTPException(status_code=404, detail="OIDC login is not enabled")
    settings = _oidc_settings()
    state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    state_cookie = request.cookies.get(OIDC_STATE_COOKIE_NAME, "")
    if not state or not code or len(state) > 256 or not hmac.compare_digest(state, state_cookie):
        raise HTTPException(status_code=401, detail="OIDC sign-in failed")
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    try:
        with connect_database(_database_url()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM oidc_login_transactions WHERE state_hash = %s AND expires_at > now() RETURNING code_verifier, nonce",
                    (state_hash,),
                )
                transaction = cursor.fetchone()
        if not transaction:
            raise ValueError("OIDC transaction expired or was already used")
        client = _oidc_client(settings)
        metadata = await client.load_server_metadata()
        token = await _exchange_oidc_code(settings, metadata, code, transaction[0])
        claims = await client.parse_id_token(token, nonce=transaction[1])
        if not claims:
            raise ValueError("OIDC response has no verified identity claims")
        issuer, subject, username, role, scopes = principal_claims(claims, settings)
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Identity service is temporarily unavailable") from None
    except Exception:
        raise HTTPException(status_code=401, detail="OIDC sign-in failed") from None

    try:
        with connect_database(_database_url()) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (USER_LIFECYCLE_LOCK,))
                cursor.execute(
                    """INSERT INTO users (id, username, password_hash, role, resource_scopes, oidc_issuer, oidc_subject, active)
                       VALUES (%s, %s, NULL, %s, %s, %s, %s, TRUE)
                       ON CONFLICT (oidc_issuer, oidc_subject) DO NOTHING
                       RETURNING id, username, role, resource_scopes, active, session_generation, reactivation_requested_at""",
                    (str(uuid.uuid4()), username, role, scopes, issuer, subject),
                )
                user = cursor.fetchone()
                if user:
                    cursor.execute(
                        "INSERT INTO audit_events (actor_id, event_type, details) VALUES (%s, 'user.provisioned', %s)",
                        (
                            user[0],
                            Jsonb(
                                {
                                    "user_id": str(user[0]),
                                    "username": user[1],
                                    "role": user[2],
                                    "resource_scopes": list(user[3]),
                                }
                            ),
                        ),
                    )
                else:
                    cursor.execute(
                        """SELECT id, username, role, resource_scopes, active, session_generation, reactivation_requested_at
                           FROM users WHERE oidc_issuer = %s AND oidc_subject = %s FOR UPDATE""",
                        (issuer, subject),
                    )
                    previous_user = cursor.fetchone()
                    if not previous_user:
                        raise HTTPException(status_code=503, detail="Identity service is temporarily unavailable")
                    changed_fields = {}
                    for field, previous_value, current_value in (
                        ("username", previous_user[1], username),
                        ("role", previous_user[2], role),
                        ("resource_scopes", list(previous_user[3]), list(scopes)),
                    ):
                        if previous_value != current_value:
                            changed_fields[field] = {"before": previous_value, "after": current_value}
                    if changed_fields:
                        cursor.execute(
                            """UPDATE users SET username = %s, role = %s, resource_scopes = %s
                               WHERE id = %s
                               RETURNING id, username, role, resource_scopes, active, session_generation, reactivation_requested_at""",
                            (username, role, scopes, previous_user[0]),
                        )
                        user = cursor.fetchone()
                        cursor.execute(
                            "INSERT INTO audit_events (actor_id, event_type, details) VALUES (%s, 'user.claims_synchronized', %s)",
                            (
                                user[0],
                                Jsonb({"user_id": str(user[0]), "changed_fields": changed_fields}),
                            ),
                        )
                    else:
                        user = previous_user
                if user and not user[4] and user[6] is None:
                    cursor.execute(
                        "UPDATE users SET reactivation_requested_at = now() WHERE id = %s AND active = FALSE AND reactivation_requested_at IS NULL RETURNING reactivation_requested_at",
                        (user[0],),
                    )
                    requested_at = cursor.fetchone()
                    if requested_at:
                        cursor.execute(
                            "INSERT INTO audit_events (actor_id, event_type, details) VALUES (%s, 'user.reactivation_requested', %s)",
                            (user[0], Jsonb({"user_id": str(user[0]), "username": user[1]})),
                        )
                    cursor.execute(
                        "SELECT id, username, role, resource_scopes, active, session_generation, reactivation_requested_at FROM users WHERE id = %s",
                        (user[0],),
                    )
                    user = cursor.fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=403, detail="OIDC identity is already mapped to another user") from None
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Identity service is temporarily unavailable") from None
    if not user or not user[4]:
        raise HTTPException(status_code=403, detail="OIDC user is inactive")
    principal = Principal(str(user[0]), user[1], user[2], tuple(user[3]), int(user[5]))
    session_token = create_session(
        principal,
        os.environ["SESSION_SIGNING_KEY"],
        ttl_seconds=OIDC_SESSION_TTL_SECONDS,
    )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "true" if os.getenv("AIOPS_ENV", "production") != "local" else "false").lower() == "true",
        samesite="lax",
        max_age=OIDC_SESSION_TTL_SECONDS,
        path="/",
    )
    response.delete_cookie(
        OIDC_STATE_COOKIE_NAME,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "true" if os.getenv("AIOPS_ENV", "production") != "local" else "false").lower() == "true",
        samesite="lax",
        path="/auth/oidc/callback",
    )
    return response


@app.post("/auth/logout")
def logout(request: Request, authorization: str | None = Header(default=None)):
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not session_token and authorization and authorization.startswith("Bearer "):
        session_token = authorization[7:]
    signing_key = os.getenv("SESSION_SIGNING_KEY", "")
    if session_token and len(signing_key) >= 32:
        try:
            expires_at = session_expiration(session_token, signing_key)
        except ValueError:
            expires_at = None
        if expires_at:
            token_hash = hashlib.sha256(session_token.encode()).hexdigest()
            with connect_database(_database_url()) as conn:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (token_hash,))
                        try:
                            token_principal = parse_session(session_token, signing_key)
                        except ValueError:
                            token_principal = None
                        if token_principal:
                            cursor.execute("SELECT id FROM users WHERE id = %s::uuid FOR UPDATE", (token_principal.user_id,))
                        cursor.execute("DELETE FROM revoked_sessions WHERE expires_at <= now()")
                        cursor.execute(
                            "INSERT INTO revoked_sessions (token_hash, expires_at) VALUES (%s, to_timestamp(%s)) ON CONFLICT (token_hash) DO NOTHING",
                            (token_hash, expires_at),
                        )
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "true" if os.getenv("AIOPS_ENV", "production") != "local" else "false").lower() == "true",
        samesite="lax",
        path="/",
    )
    return response


@app.post("/auth/login")
def login(body: LoginRequest) -> dict[str, str]:
    if _auth_mode() != "local":
        raise HTTPException(status_code=404, detail="Password login is only available in the local environment")
    key = os.getenv("SESSION_SIGNING_KEY", "")
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="Session authentication is not configured")
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, role, resource_scopes, session_generation FROM users WHERE username = %s AND active = TRUE",
                (body.username,),
            )
            user = cursor.fetchone()
    if not user or not user[2] or not verify_password(body.password, user[2]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_session(Principal(str(user[0]), user[1], user[3], tuple(user[4]), int(user[5])), key)
    return {"access_token": token, "token_type": "Bearer"}


@app.get("/api/users")
def list_users(request: Request, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, object]]]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    _require_role_permission(principal, "user:manage")
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, role, resource_scopes, active, created_at, reactivation_requested_at FROM users ORDER BY username"
            )
            rows = cursor.fetchall()
    return {
        "items": [
            {
                "id": str(row[0]),
                "username": row[1],
                "role": row[2],
                "resource_scopes": [scope.lower() for scope in row[3]],
                "active": row[4],
                "created_at": row[5].isoformat(),
                "reactivation_requested_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]
    }


@app.get("/api/incident-assignees")
def list_incident_assignees(
    request: Request,
    resource: str = Query(default=DEFAULT_RESOURCE, max_length=128),
    authorization: str | None = Header(default=None),
) -> dict[str, list[dict[str, str]]]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        resource = normalize_resource(resource)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid resource") from None
    _authorize(principal, "incident:manage", resource)
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT id, username, role FROM users
                   WHERE active = TRUE AND role IN ('operator', 'admin')
                     AND ('*' = ANY(resource_scopes) OR EXISTS (
                         SELECT 1 FROM unnest(resource_scopes) AS scopes(scope_key)
                         WHERE lower(scopes.scope_key) = %s
                     ))
                   ORDER BY username""",
                (resource,),
            )
            rows = cursor.fetchall()
    return {"items": [{"id": str(row[0]), "username": row[1], "role": row[2]} for row in rows]}


@app.post("/api/users/{user_id}/disable")
def disable_user(user_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    _require_role_permission(principal, "user:manage")
    try:
        user_uuid = str(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found") from None
    if user_uuid == principal.user_id:
        raise HTTPException(status_code=409, detail="You cannot disable your own account")

    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (USER_LIFECYCLE_LOCK,))
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                _require_role_permission(principal, "user:manage")
                if user_uuid == principal.user_id:
                    raise HTTPException(status_code=409, detail="You cannot disable your own account")
                cursor.execute("SELECT id FROM users WHERE role = 'admin' AND active = TRUE ORDER BY id FOR UPDATE")
                active_admin_ids = cursor.fetchall()
                cursor.execute(
                    "SELECT id, username, role, active FROM users WHERE id = %s FOR UPDATE",
                    (user_uuid,),
                )
                user = cursor.fetchone()
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")
                if not user[3]:
                    return {"user_id": user_uuid, "status": "already_disabled"}
                if user[2] == "admin" and len(active_admin_ids) <= 1:
                    raise HTTPException(status_code=409, detail="The last active administrator cannot be disabled")
                cursor.execute(
                    "UPDATE users SET active = FALSE, session_generation = session_generation + 1, reactivation_requested_at = NULL WHERE id = %s",
                    (user_uuid,),
                )
                cursor.execute(
                    "INSERT INTO audit_events (actor_id, event_type, details) VALUES (%s, 'user.disabled', %s)",
                    (principal.user_id, Jsonb({"user_id": user_uuid, "username": user[1]})),
                )
    return {"user_id": user_uuid, "status": "disabled"}


@app.post("/api/users/{user_id}/reactivate")
def reactivate_user(
    user_id: str,
    body: EmptyRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    del body
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    _require_role_permission(principal, "user:manage")
    try:
        user_uuid = str(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found") from None
    if user_uuid == principal.user_id:
        raise HTTPException(status_code=409, detail="You cannot reactivate your own account")

    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (USER_LIFECYCLE_LOCK,))
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                _require_role_permission(principal, "user:manage")
                if user_uuid == principal.user_id:
                    raise HTTPException(status_code=409, detail="You cannot reactivate your own account")
                cursor.execute(
                    "SELECT id, username, active, reactivation_requested_at FROM users WHERE id = %s FOR UPDATE",
                    (user_uuid,),
                )
                user = cursor.fetchone()
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")
                if user[2] or user[3] is None:
                    raise HTTPException(status_code=409, detail="No pending reactivation request")
                cursor.execute(
                    "UPDATE users SET active = TRUE, reactivation_requested_at = NULL WHERE id = %s AND active = FALSE AND reactivation_requested_at IS NOT NULL",
                    (user_uuid,),
                )
                cursor.execute(
                    "INSERT INTO audit_events (actor_id, event_type, details) VALUES (%s, 'user.reactivated', %s)",
                    (principal.user_id, Jsonb({"user_id": user_uuid, "username": user[1]})),
                )
    return {"user_id": user_uuid, "status": "reactivated"}


@app.get("/auth/me")
def who_am_i(request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "resource_scopes": list(principal.resource_scopes),
    }


@app.get("/api/incidents")
def list_incidents(
    request: Request,
    status: str | None = Query(default=None, max_length=40),
    severity: str | None = Query(default=None, max_length=20),
    assigned_to_me: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    _require_role_permission(principal, "incident:read")
    if status and status not in {"open", "investigating", "awaiting_approval", "resolved", "closed"}:
        raise HTTPException(status_code=400, detail="Invalid incident status")
    if severity and severity not in {"critical", "high", "medium", "low"}:
        raise HTTPException(status_code=400, detail="Invalid incident severity")
    cursor_values = _decode_incident_cursor(cursor) if cursor is not None else None
    filters = []
    params: list[object] = []
    if "*" not in principal.resource_scopes:
        if not principal.resource_scopes:
            return {"items": [], "limit": limit, "next_cursor": None}
        filters.append("i.resource = ANY(%s)")
        params.append(list(principal.resource_scopes))
    if status:
        filters.append("i.status = %s")
        params.append(status)
    if severity:
        filters.append("i.severity = %s")
        params.append(severity)
    if assigned_to_me:
        filters.append("i.assignee_user_id = %s")
        params.append(principal.user_id)
    if search and search.strip():
        filters.append("position(lower(%s) in lower(concat_ws(' ', i.alert_name, array_to_string(i.trace_ids, ' '), i.summary::text))) > 0")
        params.append(search.strip())
    if cursor_values:
        filters.append("(i.created_at, i.id) < (%s::timestamptz, %s::uuid)")
        params.extend(cursor_values)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""SELECT i.id, i.alert_name, i.trace_ids, i.status, i.severity,
                          i.assignee_user_id, u.username, i.created_at, i.updated_at, i.resource
                   FROM incidents i LEFT JOIN users u ON u.id = i.assignee_user_id
                   {where} ORDER BY i.created_at DESC, i.id DESC LIMIT %s""",
                (*params, limit + 1),
            )
            rows = cursor.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_incident_cursor(rows[-1][7].isoformat(), str(rows[-1][0])) if has_more and rows else None
    return {"items": [
        {"id": str(row[0]), "alert_name": row[1], "trace_ids": row[2], "status": row[3],
         "severity": row[4], "assignee_id": str(row[5]) if row[5] else None,
         "assignee_username": row[6], "created_at": row[7].isoformat(), "updated_at": row[8].isoformat(),
         "resource": row[9]}
        for row in rows
    ], "limit": limit, "next_cursor": next_cursor}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT i.id, i.alert_name, i.trace_ids, i.summary, i.status, i.severity,
                          i.assignee_user_id, u.username, i.created_at, i.updated_at, i.resource
                   FROM incidents i LEFT JOIN users u ON u.id = i.assignee_user_id WHERE i.id = %s""",
                (incident_uuid,),
            )
            incident = cursor.fetchone()
            if not incident:
                raise HTTPException(status_code=404, detail="Incident not found")
            _authorize_incident_resource(principal, "incident:read", incident[10])
            cursor.execute(
                """SELECT id, task_type, status, attempt, max_attempts, result, error_code,
                          created_at, updated_at FROM tasks WHERE incident_id = %s ORDER BY created_at""",
                (incident_uuid,),
            )
            tasks = cursor.fetchall()
            cursor.execute(
                """SELECT e.id, e.event_type, e.details, e.created_at, u.username
                   FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id
                   WHERE e.incident_id = %s ORDER BY e.created_at DESC, e.id DESC LIMIT %s""",
                (incident_uuid, 51),
            )
            events = cursor.fetchall()
            timeline_has_more = len(events) > 50
            events = events[:50]
            timeline_cursor = _encode_timeline_cursor(events[-1][3].isoformat(), events[-1][0]) if timeline_has_more and events else None
            events.reverse()
            cursor.execute(
                """SELECT a.id, a.action_id, a.status, a.parameters, a.requested_by, requester.username,
                          a.reviewed_by, reviewer.username, a.created_at, a.reviewed_at, e.status, e.error_code
                   FROM approvals a
                   LEFT JOIN users requester ON requester.id = a.requested_by
                   LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
                   LEFT JOIN action_executions e ON e.approval_id = a.id
                   WHERE a.incident_id = %s ORDER BY a.created_at""",
                (incident_uuid,),
            )
            approvals = cursor.fetchall()
    return {
        "id": str(incident[0]),
        "alert_name": incident[1],
        "trace_ids": incident[2],
        "summary": incident[3],
        "status": incident[4],
        "severity": incident[5],
        "assignee_id": str(incident[6]) if incident[6] else None,
        "assignee_username": incident[7],
        "created_at": incident[8].isoformat(),
        "updated_at": incident[9].isoformat(),
        "resource": incident[10],
        "tasks": [
            {"id": str(row[0]), "task_type": row[1], "status": row[2], "attempt": row[3], "max_attempts": row[4], "result": row[5], "error_code": row[6], "created_at": row[7].isoformat(), "updated_at": row[8].isoformat()}
            for row in tasks
        ],
        "approvals": [
            {"id": str(row[0]), "action_id": row[1], "status": row[2], "parameters": row[3], "requested_by": str(row[4]), "requested_by_username": row[5], "reviewed_by": str(row[6]) if row[6] else None, "reviewed_by_username": row[7], "created_at": row[8].isoformat(), "reviewed_at": row[9].isoformat() if row[9] else None, "execution_status": row[10], "execution_error": row[11]}
            for row in approvals
        ],
        "timeline": [
            {"event_type": row[1], "details": row[2], "created_at": row[3].isoformat(), "actor": row[4]}
            for row in events
        ],
        "timeline_next_cursor": timeline_cursor,
    }


@app.get("/api/incidents/{incident_id}/timeline")
def get_incident_timeline(
    incident_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    cursor_values = _decode_timeline_cursor(cursor) if cursor is not None else None
    with connect_database(_database_url()) as conn:
        with conn.cursor() as db_cursor:
            db_cursor.execute("SELECT resource FROM incidents WHERE id = %s", (incident_uuid,))
            incident = db_cursor.fetchone()
            if not incident:
                raise HTTPException(status_code=404, detail="Incident not found")
            _authorize_incident_resource(principal, "incident:read", incident[0])
            filters = ["e.incident_id = %s"]
            params: list[object] = [incident_uuid]
            if cursor_values:
                filters.append("(e.created_at, e.id) < (%s::timestamptz, %s::bigint)")
                params.extend(cursor_values)
            db_cursor.execute(
                f"""SELECT e.id, e.event_type, e.details, e.created_at, u.username
                    FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id
                    WHERE {' AND '.join(filters)}
                    ORDER BY e.created_at DESC, e.id DESC LIMIT %s""",
                (*params, limit + 1),
            )
            rows = db_cursor.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_timeline_cursor(rows[-1][3].isoformat(), rows[-1][0]) if has_more and rows else None
    rows.reverse()
    return {
        "items": [
            {"event_type": row[1], "details": row[2], "created_at": row[3].isoformat(), "actor": row[4]}
            for row in rows
        ],
        "next_cursor": next_cursor,
    }


@app.patch("/api/incidents/{incident_id}/triage")
def update_incident_triage(
    incident_id: str,
    body: IncidentTriageUpdate,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    updates = body.model_fields_set
    if not updates:
        raise HTTPException(status_code=422, detail="At least one triage field is required")
    if "severity" in updates and body.severity is None:
        raise HTTPException(status_code=422, detail="Severity cannot be null")
    try:
        incident_uuid = str(uuid.UUID(incident_id))
        assignee_uuid = str(uuid.UUID(body.assignee_id)) if "assignee_id" in updates and body.assignee_id else None
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid incident or assignee ID") from None

    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute(
                    "SELECT status, severity, assignee_user_id, resource FROM incidents WHERE id = %s FOR UPDATE",
                    (incident_uuid,),
                )
                incident = cursor.fetchone()
                if not incident:
                    raise HTTPException(status_code=404, detail="Incident not found")
                _authorize_incident_resource(principal, "incident:manage", incident[3])
                if incident[0] in {"resolved", "closed"}:
                    raise HTTPException(status_code=409, detail="Resolved or closed incidents cannot be triaged")

                severity = body.severity if "severity" in updates else incident[1]
                assignee_id = assignee_uuid if "assignee_id" in updates else incident[2]
                assignee_username = None
                if assignee_id is not None:
                    if "assignee_id" in updates:
                        cursor.execute(
                            """SELECT username FROM users WHERE id = %s AND active = TRUE
                                 AND role IN ('operator', 'admin')
                                 AND ('*' = ANY(resource_scopes) OR EXISTS (
                                     SELECT 1 FROM unnest(resource_scopes) AS scopes(scope_key)
                                     WHERE lower(scopes.scope_key) = %s
                                 ))""",
                            (assignee_id, incident[3]),
                        )
                    else:
                        cursor.execute("SELECT username FROM users WHERE id = %s", (assignee_id,))
                    assignee = cursor.fetchone()
                    if "assignee_id" in updates and not assignee:
                        raise HTTPException(status_code=422, detail="Assignee must be an active operator or admin with access to this resource")
                    assignee_username = assignee[0] if assignee else None

                changed = severity != incident[1] or assignee_id != incident[2]
                if changed:
                    cursor.execute(
                        """UPDATE incidents SET severity = %s, assignee_user_id = %s, updated_at = now()
                           WHERE id = %s""",
                        (severity, assignee_id, incident_uuid),
                    )
                    cursor.execute(
                        """INSERT INTO audit_events (incident_id, actor_id, event_type, details)
                           VALUES (%s, %s, 'incident.triage_updated', %s)""",
                        (incident_uuid, principal.user_id, Jsonb({
                            "previous_severity": incident[1], "severity": severity,
                            "previous_assignee_id": str(incident[2]) if incident[2] else None,
                            "assignee_id": str(assignee_id) if assignee_id else None,
                        })),
                    )
    return {
        "incident_id": incident_uuid,
        "severity": severity,
        "assignee_id": str(assignee_id) if assignee_id else None,
        "assignee_username": assignee_username,
    }


@app.patch("/api/incidents/{incident_id}/status")
def update_incident_status(
    incident_id: str,
    body: IncidentStatusUpdate,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid incident ID") from None

    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute("SELECT status, resource FROM incidents WHERE id = %s FOR UPDATE", (incident_uuid,))
                incident = cursor.fetchone()
                if not incident:
                    raise HTTPException(status_code=404, detail="Incident not found")
                _authorize_incident_resource(principal, "incident:manage", incident[1])
                current_status = incident[0]
                if body.status != current_status and body.status not in INCIDENT_STATUS_TRANSITIONS[current_status]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Incident cannot transition from {current_status} to {body.status}",
                    )
                if body.status != current_status:
                    cursor.execute(
                        "UPDATE incidents SET status = %s, updated_at = now() WHERE id = %s",
                        (body.status, incident_uuid),
                    )
                    cursor.execute(
                        """INSERT INTO audit_events (incident_id, actor_id, event_type, details)
                           VALUES (%s, %s, 'incident.status_updated', %s)""",
                        (incident_uuid, principal.user_id, Jsonb({"previous_status": current_status, "status": body.status})),
                    )
    return {"incident_id": incident_uuid, "status": body.status}


@app.get("/api/incidents/{incident_id}/retrospective")
def get_retrospective(incident_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    with connect_database(_database_url()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT i.resource, r.impact, r.root_cause, r.resolution, r.action_items, r.status,
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
    _authorize_incident_resource(principal, "incident:read", row[0])
    return {
        "impact": row[1] or "",
        "root_cause": row[2] or "",
        "resolution": row[3] or "",
        "action_items": row[4] or [],
        "status": row[5] or "draft",
        "updated_at": row[6].isoformat() if row[6] else None,
        "updated_by": row[7],
        "reviewed_at": row[8].isoformat() if row[8] else None,
        "reviewed_by": row[9],
    }


@app.put("/api/incidents/{incident_id}/retrospective")
def save_retrospective(
    incident_id: str,
    body: RetrospectiveRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    status = "reviewed" if body.reviewed else "draft"
    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute("SELECT resource FROM incidents WHERE id = %s", (incident_uuid,))
                incident = cursor.fetchone()
                if not incident:
                    raise HTTPException(status_code=404, detail="Incident not found")
                _authorize_incident_resource(principal, "incident:review", incident[0])
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
    return get_retrospective(incident_uuid, request, authorization)


@app.post("/api/tasks/{task_id}/retry", status_code=202)
def retry_task(
    task_id: str,
    request: Request,
    body: RetryTaskRequest = RetryTaskRequest(),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        task_uuid = str(uuid.UUID(task_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute(
                    """SELECT t.error_code, t.result, t.attempt, i.resource FROM tasks t
                       JOIN incidents i ON i.id = t.incident_id
                       WHERE t.id = %s AND t.status = 'failed' FOR UPDATE OF t""",
                    (task_uuid,),
                )
                failed_task = cursor.fetchone()
                if not failed_task:
                    raise HTTPException(status_code=409, detail="Only failed tasks can be retried")
                previous_error_code, previous_result, previous_attempt, incident_resource = failed_task
                _authorize_incident_resource(principal, "task:retry", incident_resource)
                result_may_have_been_charged = (
                    isinstance(previous_result, dict) and previous_result.get("possible_duplicate_charge") is True
                )
                if (
                    previous_error_code in {"holmes_outcome_unknown", "worker_outcome_unknown"}
                    or result_may_have_been_charged
                ) and not body.acknowledge_possible_duplicate_charge:
                    raise HTTPException(
                        status_code=409,
                        detail="Retrying this task may create a duplicate model charge; explicit acknowledgement is required",
                    )
                cursor.execute(
                    """UPDATE outbox_events
                       SET last_enqueued_at = NULL, available_at = now(), delivery_attempts = 0,
                           published_at = NULL, dead_lettered_at = NULL
                       WHERE payload->>'task_id' = %s RETURNING id""",
                    (task_uuid,),
                )
                dispatch_events = cursor.fetchall()
                if len(dispatch_events) != 1:
                    raise HTTPException(status_code=503, detail="Task dispatch record is unavailable")
                cursor.execute(
                    """UPDATE tasks SET status = 'queued', max_attempts = %s,
                              error_code = NULL, result = NULL, completed_at = NULL, available_at = now(),
                              lease_expires_at = NULL, updated_at = now()
                       WHERE id = %s AND status = 'failed' RETURNING incident_id""",
                    (previous_attempt + 4, task_uuid),
                )
                row = cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=409, detail="Only failed tasks can be retried")
                incident_id = row[0]
                cursor.execute(
                    "INSERT INTO audit_events (incident_id, task_id, actor_id, event_type, details) VALUES (%s, %s, %s, 'task.manual_retry_requested', %s)",
                    (
                        incident_id,
                        task_uuid,
                        principal.user_id,
                        Jsonb({
                            "previous_error_code": previous_error_code,
                            "duplicate_charge_risk_acknowledged": body.acknowledge_possible_duplicate_charge,
                        }),
                    ),
                )
    return {"task_id": task_uuid, "status": "queued"}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        task_uuid = str(uuid.UUID(task_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute(
                    """SELECT t.status, i.resource FROM tasks t
                       JOIN incidents i ON i.id = t.incident_id
                       WHERE t.id = %s FOR UPDATE OF t""",
                    (task_uuid,),
                )
                task = cursor.fetchone()
                if not task:
                    raise HTTPException(status_code=404, detail="Task not found")
                status, incident_resource = task
                _authorize_incident_resource(principal, "task:cancel", incident_resource)
                if status == "cancelled":
                    return {"task_id": task_uuid, "status": "cancelled"}
                if status not in {"queued", "retrying"}:
                    raise HTTPException(status_code=409, detail="Only queued or retrying tasks can be cancelled")
                cursor.execute(
                    """UPDATE tasks SET status = 'cancelled', error_code = NULL,
                              result = NULL, completed_at = now(), lease_expires_at = NULL,
                              updated_at = now()
                       WHERE id = %s AND status IN ('queued', 'retrying') RETURNING incident_id""",
                    (task_uuid,),
                )
                row = cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=409, detail="Task is no longer pending")
                cursor.execute(
                    """INSERT INTO audit_events
                       (incident_id, task_id, actor_id, event_type, details)
                       VALUES (%s, %s, %s, 'task.cancelled', %s)""",
                    (row[0], task_uuid, principal.user_id, Jsonb({"previous_status": status})),
                )
    return {"task_id": task_uuid, "status": "cancelled"}


@app.post("/api/incidents/{incident_id}/approvals", status_code=201)
def request_approval(
    incident_id: str,
    body: ApprovalRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    _require_demo_actions_enabled()
    try:
        incident_uuid = str(uuid.UUID(incident_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    approval_id = str(uuid.uuid4())
    parameters = {"action": body.action, "resource": body.resource, "enabled": body.enabled}
    action_key = f"{body.action}:{'on' if body.enabled else 'off'}"
    try:
        with connect_database(_database_url()) as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                    cursor.execute("SELECT resource FROM incidents WHERE id = %s FOR UPDATE", (incident_uuid,))
                    incident = cursor.fetchone()
                    if not incident:
                        raise HTTPException(status_code=404, detail="Incident not found")
                    _authorize_incident_resource(principal, "task:create", incident[0])
                    if incident[0] != "order-service":
                        raise HTTPException(status_code=409, detail="The demo action owner only supports order-service incidents")
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
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None
    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute(
                    """SELECT a.incident_id, a.requested_by, a.status, i.resource
                       FROM approvals a JOIN incidents i ON i.id = a.incident_id
                       WHERE a.id = %s FOR UPDATE OF a, i""",
                    (approval_uuid,),
                )
                approval = cursor.fetchone()
                if not approval:
                    raise HTTPException(status_code=404, detail="Approval not found")
                incident_id, requested_by, status, incident_resource = approval
                _authorize_incident_resource(principal, "approval:review", incident_resource)
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
def cancel_approval(approval_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None
    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor)
                cursor.execute(
                    """SELECT a.incident_id, a.requested_by, a.status, i.resource
                       FROM approvals a JOIN incidents i ON i.id = a.incident_id
                       WHERE a.id = %s FOR UPDATE OF a, i""",
                    (approval_uuid,),
                )
                approval = cursor.fetchone()
                if not approval:
                    raise HTTPException(status_code=404, detail="Approval not found")
                incident_id, requested_by, status, incident_resource = approval
                _authorize_incident_resource(principal, "task:create", incident_resource)
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


@contextmanager
def _serialize_action_owner(approval_uuid: str, session_token: str) -> Iterator[None]:
    """Serialize owner state and session lifecycle through the bounded demo action."""
    conn = connect_database(_database_url())
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET statement_timeout = '30s'")
            try:
                cursor.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                    (ACTION_OWNER_RESOURCE_LOCK,),
                )
                # Match lifecycle routes' lock order, then hold the token lock
                # that logout uses until the owner call and its DB outcome commit.
                cursor.execute("SELECT pg_advisory_lock(%s)", (USER_LIFECYCLE_LOCK,))
                token_hash = hashlib.sha256(session_token.encode()).hexdigest()
                cursor.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                    (token_hash,),
                )
            except psycopg.errors.QueryCanceled:
                raise HTTPException(status_code=503, detail="Session or order-service activity is still running; retry shortly") from None
        conn.commit()
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT e.approval_id FROM action_executions e
                   JOIN incidents i ON i.id = e.incident_id
                   WHERE i.resource = 'order-service'
                     AND e.status IN ('dispatching', 'rollback_pending')
                     AND e.approval_id <> %s
                   LIMIT 1""",
                (approval_uuid,),
            )
            pending = cursor.fetchone()
        conn.commit()
        if pending:
            raise HTTPException(
                status_code=409,
                detail="Another order-service action requires reconciliation before this action can run",
            )
        yield
    finally:
        # Closing the dedicated session releases every session-level lock,
        # including when lock acquisition or any later DB/owner operation fails.
        conn.close()


def _execute_approval(
    approval_id: str,
    request: Request,
    authorization: str | None = None,
    *,
    authorization_locks_held: bool = False,
) -> dict[str, object]:
    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME))
    _require_demo_actions_enabled()
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None

    execution_error: str | None = None
    incident_id: str
    action_id: str
    parameters: dict[str, object]
    execution_status: str
    before: dict[str, object] | None
    with connect_database(_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor, authorization_locks_held=authorization_locks_held)
                cursor.execute(
                    """SELECT a.incident_id, a.action_id, a.status, a.requested_by, a.reviewed_by, a.parameters, i.resource
                       FROM approvals a JOIN incidents i ON i.id = a.incident_id
                       WHERE a.id = %s FOR UPDATE OF a, i""",
                    (approval_uuid,),
                )
                approval = cursor.fetchone()
                if not approval:
                    raise HTTPException(status_code=404, detail="Approval not found")
                incident_id, action_id, status, requested_by, reviewed_by, parameters, incident_resource = approval
                _authorize_incident_resource(principal, "task:create", incident_resource)
                if incident_resource != "order-service":
                    raise HTTPException(status_code=409, detail="The demo action owner only supports order-service incidents")
                if status not in {"approved", "executed"}:
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

                cursor.execute(
                    """INSERT INTO action_executions (approval_id, incident_id, status)
                       VALUES (%s, %s, 'prepared') ON CONFLICT (approval_id) DO NOTHING""",
                    (approval_uuid, incident_id),
                )
                cursor.execute(
                    "SELECT status, before_state, error_code FROM action_executions WHERE approval_id = %s FOR UPDATE",
                    (approval_uuid,),
                )
                execution_status, before, _stored_error = cursor.fetchone()
                if status == "executed" and execution_status != "succeeded":
                    raise HTTPException(status_code=409, detail="Executed approval has no successful action record")
                if execution_status == "succeeded":
                    return {"approval_id": approval_uuid, "status": "executed", "verified": True, "chaos_mode": "on" if parameters["enabled"] else "off"}
                if execution_status in {"rolled_back", "failed"}:
                    raise HTTPException(status_code=409, detail=f"Action execution is terminal: {execution_status}")

    owner = _order_action_client()
    desired = "on" if parameters["enabled"] else "off"
    # Persist the pre-action state before any side effect. Retries and process
    # restarts reuse this snapshot and the approval UUID as the owner's key.
    if execution_status == "prepared":
        try:
            observed_before = owner.state()
        except ActionServiceError as exc:
            raise HTTPException(status_code=503, detail="Action owner state is unavailable; execution remains prepared") from exc
        if observed_before.get("resource") != "order-service" or observed_before.get("chaos_mode") not in {"on", "off"}:
            raise HTTPException(status_code=502, detail="Action owner returned an invalid pre-action state")
        with connect_database(_database_url()) as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor, authorization_locks_held=authorization_locks_held)
                    cursor.execute(
                        """UPDATE action_executions SET status = 'dispatching', before_state = %s, updated_at = now()
                           WHERE approval_id = %s AND status = 'prepared'""",
                        (Jsonb(observed_before), approval_uuid),
                    )
                    cursor.execute("SELECT status, before_state FROM action_executions WHERE approval_id = %s", (approval_uuid,))
                    execution_status, before = cursor.fetchone()

    if execution_status == "dispatching":
        try:
            prior_result = owner.operation_result(approval_uuid, bool(parameters["enabled"]))
            if prior_result is None:
                owner.set_chaos_mode(bool(parameters["enabled"]), approval_uuid)
            after = owner.state()
            verified = after.get("chaos_mode") == desired
            if prior_result is not None and not verified:
                execution_error = "owner_operation_state_mismatch"
        except ActionServiceError as exc:
            execution_error = exc.code
            # An owner error cannot establish whether the operation committed.
            # Keep it pending and rely on the journal before any retry.
            with connect_database(_database_url()) as conn:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        principal = _load_principal(
                            authorization,
                            request.cookies.get(SESSION_COOKIE_NAME),
                            cursor=cursor,
                            authorization_locks_held=authorization_locks_held,
                        )
                        cursor.execute(
                            "UPDATE action_executions SET error_code = %s, updated_at = now() WHERE approval_id = %s AND status = 'dispatching'",
                            (execution_error, approval_uuid),
                        )
                        if cursor.rowcount == 1:
                            cursor.execute(
                                "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, 'action.execution_unknown', %s)",
                                (incident_id, principal.user_id, Jsonb({"approval_id": approval_uuid, "error_code": execution_error})),
                            )
            raise HTTPException(status_code=503, detail="Action owner could not confirm the operation; execution remains pending") from exc
        if verified:
            with connect_database(_database_url()) as conn:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor, authorization_locks_held=authorization_locks_held)
                        cursor.execute(
                            """SELECT a.id, a.status, i.resource FROM approvals a
                               JOIN incidents i ON i.id = a.incident_id WHERE a.id = %s FOR UPDATE OF a, i""",
                            (approval_uuid,),
                        )
                        current = cursor.fetchone()
                        if not current or current[1] != "approved":
                            raise HTTPException(status_code=409, detail="Approval is no longer executable")
                        _authorize_incident_resource(principal, "task:create", current[2])
                        cursor.execute(
                            "UPDATE action_executions SET status = 'succeeded', error_code = NULL, updated_at = now() WHERE approval_id = %s",
                            (approval_uuid,),
                        )
                        cursor.execute("UPDATE approvals SET status = 'executed' WHERE id = %s", (approval_uuid,))
                        cursor.execute(
                            "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, 'action.verified', %s)",
                            (incident_id, principal.user_id, Jsonb({"approval_id": approval_uuid, "resource": "order-service", "action": action_id, "before_chaos_mode": before["chaos_mode"], "chaos_mode": desired})),
                        )
            return {"approval_id": approval_uuid, "status": "executed", "verified": True, "chaos_mode": desired}
        with connect_database(_database_url()) as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor, authorization_locks_held=authorization_locks_held)
                    cursor.execute(
                        "UPDATE action_executions SET status = 'rollback_pending', error_code = 'postcondition_failed', updated_at = now() WHERE approval_id = %s AND status = 'dispatching'",
                        (approval_uuid,),
                    )
        execution_status = "rollback_pending"

    if execution_status == "rollback_pending":
        rollback_error = None
        rolled_back = False
        try:
            if not before or before.get("resource") != "order-service" or before.get("chaos_mode") not in {"on", "off"}:
                raise ActionServiceError("action_before_state_unavailable")
            owner.set_chaos_mode(before["chaos_mode"] == "on", f"{approval_uuid}:rollback")
            rollback_state = owner.state()
            rolled_back = rollback_state.get("chaos_mode") == before["chaos_mode"]
            if not rolled_back:
                rollback_error = "rollback_state_not_restored"
        except ActionServiceError as exc:
            rollback_error = exc.code
        with connect_database(_database_url()) as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    principal = _load_principal(authorization, request.cookies.get(SESSION_COOKIE_NAME), cursor=cursor, authorization_locks_held=authorization_locks_held)
                    if rolled_back:
                        cursor.execute(
                            "UPDATE action_executions SET status = 'rolled_back', error_code = 'postcondition_failed', updated_at = now() WHERE approval_id = %s AND status = 'rollback_pending'",
                            (approval_uuid,),
                        )
                        cursor.execute(
                            "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, 'action.rollback_completed', %s)",
                            (incident_id, principal.user_id, Jsonb({"approval_id": approval_uuid, "resource": "order-service", "action": action_id, "before_chaos_mode": before["chaos_mode"], "rolled_back": True})),
                        )
                    else:
                        cursor.execute(
                            "UPDATE action_executions SET error_code = %s, updated_at = now() WHERE approval_id = %s AND status = 'rollback_pending'",
                            (rollback_error or "rollback_state_not_restored", approval_uuid),
                        )
                        cursor.execute(
                            "INSERT INTO audit_events (incident_id, actor_id, event_type, details) VALUES (%s, %s, 'action.rollback_failed', %s)",
                            (incident_id, principal.user_id, Jsonb({"approval_id": approval_uuid, "resource": "order-service", "action": action_id, "before_chaos_mode": before.get("chaos_mode") if before else None, "rolled_back": False, "error_code": rollback_error or "rollback_state_not_restored"})),
                        )
        if not rolled_back:
            raise HTTPException(status_code=502, detail="Rollback could not be confirmed; retry execution to resume rollback")
        raise HTTPException(status_code=502, detail="Action verification failed; original state was restored")

    raise HTTPException(status_code=409, detail=f"Action execution requires reconciliation: {execution_status}")


@app.post("/api/approvals/{approval_id}/execute")
def execute_approval(approval_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if _auth_mode() != "oidc" and authorization and authorization.startswith("Bearer "):
        session_token = authorization[7:]
    _load_principal(authorization, session_token)
    try:
        approval_uuid = str(uuid.UUID(approval_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Approval not found") from None
    with _serialize_action_owner(approval_uuid, session_token):
        return _execute_approval(approval_uuid, request, authorization, authorization_locks_held=True)


@app.post("/webhooks/openobserve", status_code=202)
@app.post("/", status_code=202, include_in_schema=False)
async def openobserve_webhook(request: Request) -> dict[str, object]:
    configured = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    previous = os.getenv("ALERT_WEBHOOK_TOKEN_PREVIOUS", "")
    supplied = request.headers.get("X-Alert-Token", "")
    if not _matches_webhook_token(supplied, configured, previous):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        name, traces, summary, fingerprint = normalize_alert(
            await _read_bounded_webhook_body(request),
            require_resource=os.getenv("AIOPS_ENV", "production") != "local",
        )
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Invalid alert payload") from exc

    database_url = _database_url()
    alert = IncidentInput(fingerprint, name, tuple(traces), summary, resource=normalize_resource(summary.get("service")))
    with connect_database(database_url) as conn:
        try:
            result = create_incident(conn, alert, max_pending_tasks=_max_pending_tasks())
        except IncidentResourceConflict:
            raise HTTPException(status_code=409, detail="Alert fingerprint is already assigned to another resource") from None
        except TaskQueueAtCapacity:
            raise HTTPException(
                status_code=503,
                detail="Alert queue is at capacity; retry the webhook later",
                headers={"Retry-After": "30"},
            ) from None
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail="Incident could not be persisted") from exc
    return {
        "accepted": True,
        "duplicate": not result["created"],
        "incident_id": result["incident_id"],
        "task_id": result["task_id"],
        "resource": alert.resource,
        "trace_ids": traces,
    }


@app.post("/webhooks/prometheus-alertmanager", status_code=202)
async def alertmanager_webhook(request: Request) -> dict[str, object]:
    configured = os.getenv("ALERTMANAGER_WEBHOOK_TOKEN", "")
    if not configured:
        raise HTTPException(status_code=404, detail="Alertmanager webhook is disabled")
    previous = os.getenv("ALERTMANAGER_WEBHOOK_TOKEN_PREVIOUS", "")
    supplied = request.headers.get("authorization", "")
    if not _matches_webhook_token(supplied, f"Bearer {configured}", f"Bearer {previous}" if previous else ""):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        alerts = normalize_alertmanager_payload(
            await _read_bounded_webhook_body(request),
            require_resource=os.getenv("AIOPS_ENV", "production") != "local",
        )
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Alertmanager payload") from exc

    accepted = []
    ignored_resolved = 0
    try:
        with connect_database(_database_url()) as conn:
            with conn.transaction():
                for item in alerts:
                    if item["status"] == "resolved":
                        ignored_resolved += 1
                        continue
                    alert = IncidentInput(
                        fingerprint=item["fingerprint"],
                        alert_name=item["alert_name"],
                        trace_ids=item["trace_ids"],
                        summary=item["summary"],
                        severity=item["severity"],
                        resource=item["resource"],
                    )
                    result = create_incident(conn, alert, max_pending_tasks=_max_pending_tasks())
                    accepted.append({
                        "incident_id": result["incident_id"],
                        "task_id": result["task_id"],
                        "duplicate": not result["created"],
                        "alert_name": alert.alert_name,
                        "resource": alert.resource,
                    })
    except IncidentResourceConflict:
        raise HTTPException(status_code=409, detail="Alert fingerprint is already assigned to another resource") from None
    except TaskQueueAtCapacity:
        raise HTTPException(
            status_code=503,
            detail="Alert queue is at capacity; retry the webhook later",
            headers={"Retry-After": "30"},
        ) from None
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Incident could not be persisted") from exc
    return {"accepted": True, "incidents": accepted, "ignored_resolved": ignored_resolved}


app.mount("/", StaticFiles(directory=str(PUBLIC_PATH), html=True), name="workbench")
