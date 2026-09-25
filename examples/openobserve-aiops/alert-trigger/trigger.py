"""OpenObserve webhook payload validation shared by the incident API."""

import hashlib
import json
import math
import os
import re
from datetime import datetime

LISTEN_PORT = int(os.environ.get("TRIGGER_PORT", "8081"))
TRACE_ID_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
MAX_TRACES = 3
MAX_BODY_BYTES = 64_000


def parse_alert_payload(body: str) -> tuple[str, list[str], str]:
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object")

    alert_name = str(payload.get("alert_name") or payload.get("name") or "openobserve-alert")
    alert_name = re.sub(r"[^A-Za-z0-9_.:/ -]", "", alert_name)[:120] or "openobserve-alert"
    raw_ids = payload.get("trace_ids", payload.get("trace_id", []))
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list):
        raw_ids = []
    trace_ids = list(dict.fromkeys(
        value.lower() for value in raw_ids
        if isinstance(value, str) and TRACE_ID_RE.fullmatch(value)
    ))[:MAX_TRACES]
    summary_values: dict[str, object] = {}
    for key in ("err_count", "alert_count"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1_000_000_000:
            summary_values[key] = value
        elif isinstance(value, str) and len(value) <= 32 and re.fullmatch(r"\d+(?:\.\d+)?", value):
            parsed = float(value) if "." in value else int(value)
            if math.isfinite(parsed) and parsed <= 1_000_000_000:
                summary_values[key] = parsed
    trigger_time = payload.get("alert_trigger_time_str")
    if isinstance(trigger_time, str) and len(trigger_time) <= 40:
        try:
            summary_values["alert_trigger_time_str"] = datetime.fromisoformat(
                trigger_time.replace("Z", "+00:00")
            ).isoformat()
        except ValueError:
            pass
    return alert_name, trace_ids, json.dumps(summary_values, ensure_ascii=True)


def alert_key(alert_name: str, trace_ids: list[str], summary: str) -> str:
    summary_values = json.loads(summary)
    if not isinstance(summary_values, dict):
        summary_values = {}
    stable_values = {key: value for key, value in summary_values.items() if key != "alert_trigger_time_str"}
    identity = [alert_name, trace_ids] if trace_ids else [alert_name, stable_values]
    material = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_alert(body: bytes) -> tuple[str, list[str], dict[str, object], str]:
    """Validate the bounded OpenObserve payload and return stable dedupe data."""
    if not body or len(body) > MAX_BODY_BYTES:
        raise ValueError("Webhook body size is invalid")
    alert_name, trace_ids, raw_summary = parse_alert_payload(body.decode("utf-8", errors="strict"))
    summary = json.loads(raw_summary)
    return alert_name, trace_ids, summary, alert_key(alert_name, trace_ids, raw_summary)


if __name__ == "__main__":
    import uvicorn

    if not os.environ.get("ALERT_WEBHOOK_TOKEN"):
        raise SystemExit("Set ALERT_WEBHOOK_TOKEN before starting the incident service")
    uvicorn.run("app:app", host="0.0.0.0", port=LISTEN_PORT)
