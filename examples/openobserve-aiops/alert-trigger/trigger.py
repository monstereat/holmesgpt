"""OpenObserve webhook payload validation shared by the incident API."""

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

LISTEN_PORT = int(os.environ.get("TRIGGER_PORT", "8081"))
TRACE_ID_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
MAX_TRACES = 3
MAX_BODY_BYTES = 64_000
DEFAULT_RESOURCE = "order-service"
RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,99}$")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def normalize_resource(value: object, *, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise ValueError("Alert service resource is required")
        return DEFAULT_RESOURCE
    if not isinstance(value, str):
        raise ValueError("Alert resource must be a string")
    resource = value.strip().lower()
    if not RESOURCE_RE.fullmatch(resource):
        raise ValueError("Alert resource has an invalid format")
    return resource


def parse_alert_payload(body: str, *, require_resource: bool = False) -> tuple[str, list[str], str]:
    payload = json.loads(body, object_pairs_hook=_unique_json_object)
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object")

    alert_name = str(payload.get("alert_name") or payload.get("name") or "openobserve-alert")
    alert_name = re.sub(r"[^A-Za-z0-9_.:/ -]", "", alert_name)[:120] or "openobserve-alert"
    raw_ids = payload.get("trace_ids", payload.get("trace_id", []))
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list):
        raw_ids = []
    trace_ids = sorted({
        value.lower() for value in raw_ids
        if isinstance(value, str) and TRACE_ID_RE.fullmatch(value)
    })[:MAX_TRACES]
    summary_values: dict[str, object] = {}
    resource = normalize_resource(payload.get("service"), required=require_resource)
    if resource != DEFAULT_RESOURCE:
        summary_values["service"] = resource
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
            parsed_trigger_time = datetime.fromisoformat(trigger_time.replace("Z", "+00:00"))
            if parsed_trigger_time.tzinfo is not None and parsed_trigger_time.utcoffset() is not None:
                summary_values["alert_trigger_time_str"] = parsed_trigger_time.astimezone(
                    timezone.utc
                ).isoformat()
        except ValueError:
            pass
    return alert_name, trace_ids, json.dumps(summary_values, ensure_ascii=True)


def alert_key(alert_name: str, trace_ids: list[str], summary: str) -> str:
    trace_ids = sorted(set(trace_ids))
    summary_values = json.loads(summary)
    if not isinstance(summary_values, dict):
        summary_values = {}
    stable_values = {key: value for key, value in summary_values.items() if key != "alert_trigger_time_str"}
    resource = stable_values.pop("service", DEFAULT_RESOURCE)
    identity = [alert_name, trace_ids] if trace_ids else [alert_name, stable_values]
    if resource != DEFAULT_RESOURCE:
        identity.insert(0, resource)
    material = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_alert(body: bytes, *, require_resource: bool = False) -> tuple[str, list[str], dict[str, object], str]:
    """Validate the bounded OpenObserve payload and return stable dedupe data."""
    if not body or len(body) > MAX_BODY_BYTES:
        raise ValueError("Webhook body size is invalid")
    alert_name, trace_ids, raw_summary = parse_alert_payload(
        body.decode("utf-8", errors="strict"), require_resource=require_resource
    )
    summary = json.loads(raw_summary)
    return alert_name, trace_ids, summary, alert_key(alert_name, trace_ids, raw_summary)


def _annotation_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()[:limit]
    return clean or None


def _safe_annotation_url(value: object) -> str | None:
    clean = _annotation_text(value, 2048)
    if not clean or any(character.isspace() for character in clean):
        return None
    try:
        parsed = urlsplit(clean)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
    ):
        return None
    return clean


def normalize_alertmanager_payload(body: bytes, *, require_resource: bool = False) -> list[dict[str, object]]:
    """Normalize a bounded Prometheus Alertmanager webhook v4 notification."""
    if not body or len(body) > MAX_BODY_BYTES:
        raise ValueError("Webhook body size is invalid")
    payload = json.loads(body.decode("utf-8", errors="strict"), object_pairs_hook=_unique_json_object)
    if not isinstance(payload, dict) or payload.get("version") != "4":
        raise ValueError("Unsupported Alertmanager webhook payload")
    group_key = payload.get("groupKey")
    if not isinstance(group_key, str) or not group_key or len(group_key) > 4096:
        raise ValueError("Alertmanager notification is missing a valid groupKey")
    try:
        group_key.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Alertmanager groupKey contains invalid Unicode") from exc
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts or len(alerts) > 100:
        raise ValueError("Alertmanager notification must contain 1 to 100 alerts")
    common_annotations = payload.get("commonAnnotations", {})
    if not isinstance(common_annotations, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in common_annotations.items()
    ):
        raise ValueError("Invalid Alertmanager common annotations")
    try:
        for key, value in common_annotations.items():
            key.encode("utf-8", errors="strict")
            value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Alertmanager common annotations contain invalid Unicode") from exc
    truncated = payload.get("truncatedAlerts", 0)
    if isinstance(truncated, bool) or not isinstance(truncated, int) or truncated < 0:
        raise ValueError("Invalid Alertmanager truncation count")
    if truncated:
        raise ValueError("Alertmanager notification was truncated; reduce alert group size")

    normalized = []
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get("status") not in {"firing", "resolved"}:
            raise ValueError("Invalid Alertmanager alert")
        labels = alert.get("labels")
        annotations = alert.get("annotations", {})
        if not isinstance(labels, dict) or not isinstance(annotations, dict):
            raise ValueError("Invalid Alertmanager alert metadata")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()):
            raise ValueError("Invalid Alertmanager labels")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in annotations.items()):
            raise ValueError("Invalid Alertmanager annotations")
        try:
            for metadata in (labels, annotations):
                for key, value in metadata.items():
                    key.encode("utf-8", errors="strict")
                    value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("Alertmanager metadata contains invalid Unicode") from exc
        raw_name = labels.get("alertname")
        if not isinstance(raw_name, str) or not raw_name:
            raise ValueError("Alertmanager alert is missing alertname")
        name = re.sub(r"[^A-Za-z0-9_.:/ -]", "", raw_name)[:120] or "prometheus-alert"

        starts_at = alert.get("startsAt")
        if not isinstance(starts_at, str) or len(starts_at) > 40:
            raise ValueError("Alertmanager alert is missing a valid startsAt")
        try:
            parsed_starts_at = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Alertmanager alert is missing a valid startsAt") from exc
        if parsed_starts_at.tzinfo is None:
            raise ValueError("Alertmanager alert is missing a timezone in startsAt")
        episode_started_at = parsed_starts_at.astimezone(timezone.utc).isoformat()

        raw_fingerprint = alert.get("fingerprint")
        if isinstance(raw_fingerprint, str) and re.fullmatch(r"[A-Fa-f0-9]{1,128}", raw_fingerprint):
            identity = raw_fingerprint.lower()
        else:
            identity = json.dumps(labels, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        fingerprint_material = json.dumps(
            ["alertmanager", group_key, identity, episode_started_at],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()

        trace_value = labels.get("trace_id")
        trace_ids = [trace_value.lower()] if isinstance(trace_value, str) and TRACE_ID_RE.fullmatch(trace_value) else []
        severity_value = labels.get("severity")
        severity = severity_value.lower().strip() if isinstance(severity_value, str) else "medium"
        severity = {
            "critical": "critical",
            "high": "high",
            "error": "high",
            "medium": "medium",
            "warning": "medium",
            "low": "low",
            "info": "low",
        }.get(severity, "medium")

        resource = normalize_resource(labels.get("service"), required=require_resource)
        summary: dict[str, object] = {"source": "prometheus-alertmanager", "service": resource}
        summary["alert_severity"] = severity
        for key in ("cluster", "service", "namespace", "job", "instance"):
            if key == "service":
                continue
            value = labels.get(key)
            if isinstance(value, str):
                clean = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()[:160]
                if clean:
                    summary[key] = clean
        annotation_summary = annotations.get("summary", common_annotations.get("summary"))
        clean_summary = _annotation_text(annotation_summary, 500)
        if clean_summary:
            summary["alert_summary"] = clean_summary
        description = _annotation_text(
            annotations.get("description", common_annotations.get("description")), 1200
        )
        if description:
            summary["alert_description"] = description
        for annotation, field in (("runbook_url", "runbook_url"), ("dashboard_url", "dashboard_url")):
            annotation_value = annotations.get(annotation, common_annotations.get(annotation))
            safe_url = _safe_annotation_url(annotation_value)
            if safe_url:
                summary[field] = safe_url
        summary["alert_trigger_time_str"] = episode_started_at

        normalized.append({
            "status": alert["status"],
            "fingerprint": fingerprint,
            "alert_name": name,
            "resource": resource,
            "trace_ids": tuple(trace_ids),
            "summary": summary,
            "severity": severity,
        })
    return normalized


if __name__ == "__main__":
    import uvicorn

    if not os.environ.get("ALERT_WEBHOOK_TOKEN"):
        raise SystemExit("Set ALERT_WEBHOOK_TOKEN before starting the incident service")
    uvicorn.run("app:app", host="0.0.0.0", port=LISTEN_PORT)
