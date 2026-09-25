"""Seed synthetic evaluation evidence into the local OpenObserve test stream."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


TRACE_ID = re.compile(r"\b[0-9a-f]{16,32}\b", re.IGNORECASE)
MAX_RECORDS = 100
MAX_RESPONSE_BYTES = 65_536


def build_records(cases: list[dict[str, Any]], run_id: str, timestamp_us: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    records = []
    trace_ids = {}
    for case in cases:
        trace_id = hashlib.sha256(f"holmes-aiops-eval:{run_id}:{case['id']}".encode()).hexdigest()[:32]
        trace_ids[case["id"]] = trace_id
        for evidence in case["evidence"]:
            message = TRACE_ID.sub(trace_id, evidence)
            records.append({
                "_timestamp": timestamp_us,
                "timestamp": datetime.fromtimestamp(timestamp_us / 1_000_000, timezone.utc).isoformat(),
                "trace_id": trace_id,
                "service": case["service"],
                "service_name": case["service"],
                "alert_name": case["alert"],
                "event_type": "aiops_eval_evidence",
                "evaluation_case_id": case["id"],
                "evaluation_run_id": run_id,
                "evaluation_source": "synthetic_fixture",
                "message": message,
            })
    if not records or len(records) > MAX_RECORDS:
        raise ValueError("Synthetic evaluation record count is outside the allowed bound")
    return records, trace_ids


def _validate_local_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port != 5080
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Synthetic evidence can only be sent to local OpenObserve on port 5080")
    return f"http://{parsed.netloc}"


def seed_local_evidence(
    cases: list[dict[str, Any]],
    *,
    base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    target = _validate_local_url(base_url or os.environ.get("OPENOBSERVE_INGEST_URL", "http://127.0.0.1:5080"))
    username = username if username is not None else os.environ.get("ZO_ROOT_USER_EMAIL", "")
    password = password if password is not None else os.environ.get("ZO_ROOT_USER_PASSWORD", "")
    if not username or not password:
        raise ValueError("Local OpenObserve credentials are required")

    run_id = uuid.uuid4().hex
    timestamp_us = time.time_ns() // 1_000
    records, trace_ids = build_records(cases, run_id, timestamp_us)
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{target}/api/default/app_logs/_json",
        data=json.dumps(records, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=15) as response:
            if response.status != 200:
                raise ValueError(f"OpenObserve ingestion returned HTTP {response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"OpenObserve ingestion returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Local OpenObserve ingestion request failed") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("OpenObserve ingestion response exceeded the size limit")
    try:
        result = json.loads(raw)
        statuses = result["status"]
        successful = sum(int(item["successful"]) for item in statuses)
        failed = sum(int(item["failed"]) for item in statuses)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise ValueError("OpenObserve returned an invalid ingestion response") from None
    if successful != len(records) or failed != 0:
        raise ValueError("OpenObserve did not accept every synthetic evaluation record")
    return {
        "run_id": run_id,
        "timestamp_us": timestamp_us,
        "record_count": successful,
        "trace_ids": trace_ids,
    }
