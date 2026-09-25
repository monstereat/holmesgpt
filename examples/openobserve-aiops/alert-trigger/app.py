"""Authenticated webhook API and local database migration lifecycle."""

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
import psycopg
from fastapi import FastAPI, HTTPException, Request

from models import IncidentInput
from store import create_incident
from trigger import MAX_BODY_BYTES, normalize_alert

MIGRATIONS_PATH = Path(__file__).parent / "migrations"


def apply_migrations(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for path in sorted(MIGRATIONS_PATH.glob("*.sql")):
            version = path.stem
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
                if cursor.fetchone():
                    continue
            script = path.read_text(encoding="utf-8").strip()
            if script.upper().startswith("BEGIN;"):
                script = script[6:].lstrip()
            if script.upper().endswith("COMMIT;"):
                script = script[:-7].rstrip()
            with conn.transaction():
                conn.execute(script, prepare=False)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING", (version,))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        apply_migrations(database_url)
        from worker import start_outbox_dispatcher

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
