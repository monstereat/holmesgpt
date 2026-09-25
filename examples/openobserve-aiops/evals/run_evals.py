#!/usr/bin/env python3
"""Replay the synthetic corpus as a mock report or call the configured Holmes API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXAMPLE = Path(__file__).resolve().parents[1]
ALERT_TRIGGER = EXAMPLE / "alert-trigger"
REPOSITORY_ROOT = EXAMPLE.parent.parent
sys.path.insert(0, str(ALERT_TRIGGER))

from evaluation import build_report, has_evaluation_record  # noqa: E402


def validate_local_holmes_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port != 5050
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Live evaluations can only call local Holmes on port 5050")
    return f"http://{parsed.netloc}"


def load_evaluation_contexts() -> dict[str, dict[str, Any]]:
    context_path = EXAMPLE / "runbooks" / "evaluation-contexts.json"
    raw = json.loads(context_path.read_text(encoding="utf-8"))
    if raw.get("source") != "examples/openobserve-aiops/evals/known_root_causes.json":
        raise SystemExit("evaluation context source did not match the local corpus")
    contexts = {}
    for item in raw.get("contexts", []):
        runbook = (REPOSITORY_ROOT / item["runbook"]).resolve()
        skill_path = (REPOSITORY_ROOT / item["skill"]).resolve() if item.get("skill") else None
        if not runbook.is_relative_to(REPOSITORY_ROOT) or not runbook.is_file():
            raise SystemExit("evaluation runbook must be a repository file")
        runbook_text = runbook.read_text(encoding="utf-8")
        if len(runbook_text) > 12_000:
            raise SystemExit("evaluation runbook exceeded the size limit")
        skill = None
        if skill_path:
            if not skill_path.is_relative_to(REPOSITORY_ROOT) or not skill_path.is_file():
                raise SystemExit("evaluation skill must be a repository file")
            skill = {
                "path": item["skill"],
                "content": skill_path.read_text(encoding="utf-8"),
            }
        case_id = item["case_id"]
        if case_id in contexts:
            raise SystemExit("evaluation context contains a duplicate case")
        contexts[case_id] = {
            **item,
            "runbook_context": {
                "source": "repository_runbook",
                "path": item["runbook"],
                "content": runbook_text,
            },
            "skill_context": skill,
        }
    return contexts


def run_live(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    base_url = os.environ.get("HOLMES_API_URL", "").strip()
    api_key = os.environ.get("HOLMES_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("live mode requires HOLMES_API_URL and HOLMES_API_KEY")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("live mode requires DEEPSEEK_API_KEY in the caller environment")
    try:
        base_url = validate_local_holmes_url(base_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    try:
        with urllib.request.urlopen(f"{base_url}/healthz", timeout=5) as response:
            if response.status != 200:
                raise SystemExit("local Holmes health check failed; no OpenObserve data was written")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SystemExit("local Holmes health check failed; no OpenObserve data was written") from None

    from seed_local_evidence import seed_local_evidence

    contexts = load_evaluation_contexts()

    try:
        dataset = seed_local_evidence(cases, contexts=contexts)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    from holmes_client import HolmesClient
    from task_errors import PermanentTaskError, RetryableTaskError

    client = HolmesClient(base_url=base_url, api_key=api_key)
    results = {}
    for case in cases:
        trace_id = dataset["trace_ids"][case["id"]]
        summary = {
            "service": case["service"],
            "symptom": case["symptom"],
            "fixture_source": "synthetic_known_root_cause_case",
            "evaluation_case_id": case["id"],
            "evaluation_run_id": dataset["run_id"],
            "evaluation_timestamp_us": dataset["timestamp_us"],
        }
        if case["id"] in contexts:
            summary["release_context"] = {
                "source": "synthetic_fixture",
                "event_type": "release_deployed",
                "release": contexts[case["id"]]["release"],
                "commit_sha": contexts[case["id"]].get("commit_sha"),
                "changed_files": contexts[case["id"]].get("changed_files", []),
            }
            summary["runbook_context"] = contexts[case["id"]]["runbook_context"]
            if contexts[case["id"]]["skill_context"]:
                summary["skill_context"] = contexts[case["id"]]["skill_context"]
        try:
            results[case["id"]] = client.investigate(
                {
                    "alert_name": case["alert"],
                    "trace_ids": [trace_id],
                    "summary": summary,
                },
                evaluation=True,
            )
        except (PermanentTaskError, RetryableTaskError, ValueError) as exc:
            # Store only a stable safe code, never upstream details.
            code = getattr(exc, "code", "evaluation_request_failed")
            results[case["id"]] = {"evidence": [], "evidence_status": "unavailable", "error_code": str(code)[:64]}
        case_evidence = results[case["id"]].get("evidence", [])
        results[case["id"]]["evaluation_evidence_match"] = has_evaluation_record(
            case_evidence,
            run_id=dataset["run_id"],
            case_id=case["id"],
        )
        results[case["id"]]["evaluation_release_event_match"] = (
            has_evaluation_record(
                case_evidence,
                run_id=dataset["run_id"],
                case_id=case["id"],
                event_type="release_deployed",
            )
            if case["id"] in contexts else None
        )
        results[case["id"]].update({
            "evaluation_run_id": dataset["run_id"],
            "evaluation_trace_id": trace_id,
            "seeded_record_count": dataset["record_count"],
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--output", type=Path, help="Report destination (defaults to stdout)")
    parser.add_argument("--confirm-live", action="store_true", help="Confirm that live mode may seed local fixtures and make up to 20 Holmes/model requests")
    args = parser.parse_args()

    cases = json.loads((Path(__file__).with_name("known_root_causes.json")).read_text(encoding="utf-8"))
    if args.mode == "live" and not args.confirm_live:
        parser.error("live mode seeds synthetic evidence locally and sends up to 20 requests; pass --confirm-live to proceed")
    results = run_live(cases) if args.mode == "live" else {}
    report = build_report(cases, mode=args.mode, results=results)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {len(report['cases'])} {args.mode} case reports to {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
