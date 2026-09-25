"""Report helpers for the bounded OpenObserve/Holmes evaluation corpus."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


CLUSTER_BY_CASE = {
    "order-inventory-negative-stock": "domain.inventory",
    "order-database-pool-exhaustion": "database.pool",
    "order-database-deploy-mismatch": "database.schema_deployment",
    "order-upstream-payment-timeout": "dependency.timeout",
    "order-dns-resolution-failure": "network.dns",
    "order-tls-certificate-expired": "security.tls",
    "order-redis-cache-outage": "cache.connectivity",
    "order-queue-consumer-crashloop": "queue.schema_compatibility",
    "order-queue-publish-rejected": "queue.authorization",
    "order-rate-limit-provider": "dependency.rate_limit",
    "order-secret-rotation-stale-client": "security.credential_rotation",
    "order-disk-full-log-volume": "capacity.storage",
    "order-memory-oom-kill": "capacity.memory",
    "order-cpu-throttling": "capacity.cpu",
    "order-feature-flag-null-config": "configuration.feature_flag",
    "order-middleware-header-case": "middleware.header_handling",
    "order-cache-stampede": "cache.stampede",
    "order-clock-skew-signature": "security.time_sync",
    "order-retry-non-idempotent-side-effect": "payment.idempotency",
    "order-release-regression": "deployment.regression",
}
CLUSTER_TAXONOMY = "curated_fixture_taxonomy"
TRACE_ID = re.compile(r"\b[0-9a-f]{16,32}\b", re.IGNORECASE)


def trace_ids(case: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for item in case.get("evidence", []):
        for trace_id in TRACE_ID.findall(item):
            if trace_id not in found:
                found.append(trace_id)
    return found


def build_case_report(case: dict[str, Any], *, mode: str, live_result: dict[str, Any] | None = None) -> dict[str, Any]:
    if mode not in {"mock", "live"}:
        raise ValueError("mode must be mock or live")
    cluster = CLUSTER_BY_CASE.get(case.get("id"))
    if cluster is None:
        raise ValueError("unknown evaluation case")

    is_live = mode == "live"
    result = live_result or {}
    evidence_source = "live_openobserve" if is_live else "synthetic_fixture"
    evaluated_trace_ids = [result["evaluation_trace_id"]] if is_live and result.get("evaluation_trace_id") else trace_ids(case)
    return {
        "case_id": case["id"],
        "source": "known_root_causes.json#" + case["id"],
        "mode": mode,
        "input": {
            "alert": case["alert"],
            "service": case["service"],
            "symptom": case["symptom"],
            "trace_ids": evaluated_trace_ids,
        },
        "evidence": result.get("evidence", []) if is_live else case["evidence"],
        "evidence_source": evidence_source,
        "evidence_status": result.get("evidence_status", "not_run") if is_live else "fixture_only",
        "diagnosis": result.get("analysis") if is_live else None,
        "reference_diagnosis": case["root_cause"],
        "expected_findings": case["expected_findings"],
        "assumptions": [],
        "unsupported_claims": case["unsupported_claims"],
        "safe_next_step": case["safe_next_step"],
        "cluster": cluster,
        "cluster_source": CLUSTER_TAXONOMY,
        "scoring": "not_scored",
        "error_code": result.get("error_code") if is_live else None,
    }


def build_report(cases: list[dict[str, Any]], *, mode: str, results: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if len(cases) != len(CLUSTER_BY_CASE) or {case.get("id") for case in cases} != set(CLUSTER_BY_CASE):
        raise ValueError("evaluation corpus must contain exactly the curated 20 cases")
    results = results or {}
    reports = [build_case_report(case, mode=mode, live_result=results.get(case["id"])) for case in cases]
    evaluation_run_id = next((result.get("evaluation_run_id") for result in results.values() if result.get("evaluation_run_id")), None)
    seeded_record_count = next((result.get("seeded_record_count") for result in results.values() if result.get("evaluation_run_id") == evaluation_run_id), None)
    return {
        "schema_version": "1.0.0",
        "mode": mode,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "case_source": "synthetic_fixture",
        "evaluation_run_id": evaluation_run_id,
        "seeded_record_count": seeded_record_count,
        "counts": {
            "total": len(reports),
            "live_errors": sum(item["error_code"] is not None for item in reports),
        },
        "scoring": "not_scored",
        "cluster_taxonomy": CLUSTER_TAXONOMY,
        "cases": reports,
    }
