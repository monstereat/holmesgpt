"""Measure PostgreSQL incident-admission storage on the isolated Compose test database."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from urllib.parse import urlsplit

import psycopg

from migration_runner import apply_migrations
from models import IncidentInput
from store import TaskQueueAtCapacity, create_incident


MAX_REQUESTS = 5_000
MAX_CONCURRENCY = 64


def validate_test_database_url(database_url: str) -> None:
    """Allow only the dedicated test DB service alias and its fixed identity."""
    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError:
        raise ValueError("AIOPS_TEST_DATABASE_URL is not a valid test database URL") from None
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != "postgres"
        or parsed.username != "aiops"
        or parsed.path != "/aiops_test"
        or port not in {None, 5432}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("benchmark only permits the Compose test database postgres/aiops/aiops_test")


def validate_options(requests: int, concurrency: int, capacity: int) -> None:
    if not 2 <= requests <= MAX_REQUESTS:
        raise ValueError(f"requests must be between 2 and {MAX_REQUESTS}")
    if not 1 <= concurrency <= min(MAX_CONCURRENCY, requests):
        raise ValueError(f"concurrency must be between 1 and min({MAX_CONCURRENCY}, requests)")
    if not 1 <= capacity < requests:
        raise ValueError("capacity must be positive and less than requests")


def percentile(samples: list[float], fraction: float) -> float:
    if not samples or not 0 < fraction <= 1:
        raise ValueError("percentile requires samples and a fraction in (0, 1]")
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index] * 1000, 3)


def table_counts(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("incidents", "tasks", "outbox_events")
        }


def cleanup_run(database_url: str, alert_prefix: str) -> None:
    with psycopg.connect(database_url) as conn, conn.transaction(), conn.cursor() as cursor:
        cursor.execute(
            """DELETE FROM outbox_events
               WHERE aggregate_type = 'incident'
                 AND aggregate_id IN (SELECT id FROM incidents WHERE alert_name LIKE %s)""",
            (f"{alert_prefix}%",),
        )
        cursor.execute(
            "DELETE FROM tasks WHERE incident_id IN (SELECT id FROM incidents WHERE alert_name LIKE %s)",
            (f"{alert_prefix}%",),
        )
        cursor.execute("DELETE FROM incidents WHERE alert_name LIKE %s", (f"{alert_prefix}%",))


def run_benchmark(database_url: str, requests: int, concurrency: int, capacity: int) -> dict[str, Any]:
    validate_test_database_url(database_url)
    validate_options(requests, concurrency, capacity)
    os.environ["AIOPS_ENV"] = "local"
    apply_migrations(database_url)
    initial_counts = table_counts(database_url)
    if any(initial_counts.values()):
        raise RuntimeError("isolated benchmark database must have no incidents, tasks, or outbox rows")

    run_id = secrets.token_hex(8)
    alert_prefix = f"admission-benchmark-{run_id}-"
    start_barrier = Barrier(concurrency)
    samples: list[float] = []
    accepted = 0
    rejected = 0
    futures = []
    started = time.perf_counter()

    def submit(index: int) -> tuple[str, float]:
        if index < concurrency:
            start_barrier.wait(timeout=30)
        alert_name = f"{alert_prefix}{index:05d}"
        fingerprint = hashlib.sha256(alert_name.encode("utf-8")).hexdigest()
        alert = IncidentInput(fingerprint=fingerprint, alert_name=alert_name)
        request_started = time.perf_counter()
        try:
            with psycopg.connect(database_url) as conn:
                create_incident(conn, alert, max_pending_tasks=capacity)
            outcome = "accepted"
        except TaskQueueAtCapacity:
            outcome = "rejected"
        return outcome, time.perf_counter() - request_started

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(submit, index) for index in range(requests)]
            for future in futures:
                outcome, duration = future.result()
                samples.append(duration)
                if outcome == "accepted":
                    accepted += 1
                else:
                    rejected += 1
        elapsed = time.perf_counter() - started
    finally:
        cleanup_run(database_url, alert_prefix)

    remaining_counts = table_counts(database_url)
    if any(remaining_counts.values()):
        raise RuntimeError("benchmark cleanup did not restore empty incident/task/outbox tables")

    return {
        "schema_version": "1.0.0",
        "benchmark": "postgres-incident-admission-store",
        "scope": "isolated-compose-test-postgres-only",
        "production_slo": False,
        "http_or_worker_path_included": False,
        "request_count": requests,
        "concurrency": concurrency,
        "configured_pending_capacity": capacity,
        "accepted": accepted,
        "rejected_at_capacity": rejected,
        "elapsed_seconds": round(elapsed, 6),
        "requests_per_second": round(requests / elapsed, 3),
        "accepted_per_second": round(accepted / elapsed, 3),
        "admission_latency_ms": {
            "p50": percentile(samples, 0.50),
            "p95": percentile(samples, 0.95),
            "p99": percentile(samples, 0.99),
        },
        "cleanup_verified": True,
        "limitations": [
            "Includes client connection setup and PostgreSQL admission transaction latency.",
            "Excludes HTTP, authentication, outbox dispatch, Redis/Celery, worker, Holmes, and model latency.",
            "Local Docker measurements do not establish production capacity or SLOs.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--capacity", type=int, required=True)
    parser.add_argument("--confirm-isolated-test-db", action="store_true", required=True)
    args = parser.parse_args()
    try:
        database_url = os.getenv("AIOPS_TEST_DATABASE_URL", "")
        if not database_url:
            raise ValueError("AIOPS_TEST_DATABASE_URL is required")
        report = run_benchmark(database_url, args.requests, args.concurrency, args.capacity)
    except (OSError, ValueError, RuntimeError, psycopg.Error) as exc:
        print(f"benchmark refused or failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
