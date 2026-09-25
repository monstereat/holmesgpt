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


def has_evaluation_record(
    evidence: list[dict[str, Any]],
    *,
    run_id: str,
    case_id: str,
    event_type: str | None = None,
    seed_timestamp_us: int | None = None,
) -> bool:
    pending: list[Any] = list(evidence)
    inspected = 0
    while pending and inspected < 10_000:
        value = pending.pop()
        inspected += 1
        if isinstance(value, dict):
            if (
                value.get("evaluation_run_id") == run_id
                and value.get("evaluation_case_id") == case_id
                and (event_type is None or value.get("event_type") == event_type)
            ):
                return True
            if value.get("tool_name") == "openobserve_search_logs" and value.get("status") == "success":
                params = value.get("params")
                data = value.get("data")
                sql = params.get("sql") if isinstance(params, dict) else None
                hits = data.get("hits") if isinstance(data, dict) else None
                start_time = params.get("start_time") if isinstance(params, dict) else None
                end_time = params.get("end_time") if isinstance(params, dict) else None
                if (
                    isinstance(sql, str)
                    and isinstance(hits, list)
                    and hits
                    and isinstance(start_time, int)
                    and isinstance(end_time, int)
                    and (seed_timestamp_us is None or start_time <= seed_timestamp_us < end_time)
                    and _query_has_evaluation_scope(sql, run_id, case_id, event_type, hits)
                ):
                    return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


def cross_case_exposure_case_ids(
    evidence: list[dict[str, Any]], *, run_id: str, case_id: str
) -> list[str]:
    exposed: set[str] = set()
    pending: list[Any] = list(evidence)
    inspected = 0
    while pending and inspected < 10_000:
        value = pending.pop()
        inspected += 1
        if isinstance(value, dict):
            if value.get("tool_name") == "openobserve_search_logs" and value.get("status") == "success":
                data = value.get("data")
                hits = data.get("hits") if isinstance(data, dict) else None
                if isinstance(hits, list):
                    for hit in hits:
                        if not isinstance(hit, dict) or hit.get("evaluation_run_id") != run_id:
                            continue
                        foreign_case_id = hit.get("evaluation_case_id")
                        if isinstance(foreign_case_id, str) and foreign_case_id != case_id:
                            exposed.add(foreign_case_id)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return sorted(exposed)


def unscoped_successful_search_count(
    evidence: list[dict[str, Any]], *, run_id: str, case_id: str
) -> int:
    unscoped = 0
    pending: list[Any] = list(evidence)
    inspected = 0
    while pending and inspected < 10_000:
        value = pending.pop()
        inspected += 1
        if isinstance(value, dict):
            if value.get("tool_name") == "openobserve_search_logs" and value.get("status") == "success":
                params = value.get("params")
                sql = params.get("sql") if isinstance(params, dict) else None
                if not isinstance(sql, str) or not _sql_has_exact_case_scope(sql, run_id, case_id):
                    unscoped += 1
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return unscoped


def _sql_has_exact_case_scope(sql: str, run_id: str, case_id: str) -> bool:
    normalized = re.sub(r'["`]', "", sql).lower()
    if re.search(r"\bor\b", normalized):
        return False
    run_predicate = rf"evaluation_run_id\s*=\s*'{re.escape(run_id.lower())}'"
    case_predicate = rf"evaluation_case_id\s*=\s*'{re.escape(case_id.lower())}'"
    return bool(
        re.search(run_predicate + r"\s+and\s+" + case_predicate, normalized)
        or re.search(case_predicate + r"\s+and\s+" + run_predicate, normalized)
    )


def _query_has_evaluation_scope(
    sql: str,
    run_id: str,
    case_id: str,
    event_type: str | None,
    hits: list[Any],
) -> bool:
    if not _sql_has_exact_case_scope(sql, run_id, case_id):
        return False
    if event_type is None:
        return True
    event_predicate = rf"event_type\s*=\s*'{re.escape(event_type.lower())}'"
    return bool(
        re.search(event_predicate, normalized)
        or any(isinstance(hit, dict) and hit.get("event_type") == event_type for hit in hits)
    )


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
        "evaluation_evidence_match": result.get("evaluation_evidence_match") if is_live else None,
        "evaluation_release_event_match": result.get("evaluation_release_event_match") if is_live else None,
        "cross_case_exposure_case_ids": result.get("cross_case_exposure_case_ids", []) if is_live else [],
        "unscoped_successful_search_count": result.get("unscoped_successful_search_count", 0) if is_live else 0,
        "diagnosis": result.get("analysis") if is_live else None,
        "reference_diagnosis": case["root_cause"],
        "reference_expected_findings": case["expected_findings"],
        "assumptions": None,
        "reference_unsupported_claims": case["unsupported_claims"],
        "reference_safe_next_step": case["safe_next_step"],
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
        "schema_version": "1.3.0",
        "mode": mode,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "case_source": "synthetic_fixture",
        "evaluation_run_id": evaluation_run_id,
        "seeded_record_count": seeded_record_count,
        "counts": {
            "total": len(reports),
            "live_errors": sum(item["error_code"] is not None for item in reports),
            "matching_case_evidence": sum(item["evaluation_evidence_match"] is True for item in reports),
            "release_event_cases": sum(item["evaluation_release_event_match"] is not None for item in reports),
            "matching_release_events": sum(item["evaluation_release_event_match"] is True for item in reports),
            "cases_with_cross_case_exposure": sum(bool(item["cross_case_exposure_case_ids"]) for item in reports),
            "cross_case_exposure_case_count": sum(len(item["cross_case_exposure_case_ids"]) for item in reports),
            "cases_with_unscoped_successful_searches": sum(item["unscoped_successful_search_count"] > 0 for item in reports),
            "unscoped_successful_search_count": sum(item["unscoped_successful_search_count"] for item in reports),
        },
        "retrieval_coverage": {
            "case_evidence_matches": sum(item["evaluation_evidence_match"] is True for item in reports),
            "release_event_matches": sum(item["evaluation_release_event_match"] is True for item in reports),
        },
        "scoring": "not_scored",
        "cluster_taxonomy": CLUSTER_TAXONOMY,
        "cases": reports,
    }
