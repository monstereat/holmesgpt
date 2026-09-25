#!/usr/bin/env python3
"""Replay the synthetic corpus as a mock report or call the configured Holmes API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


EXAMPLE = Path(__file__).resolve().parents[1]
ALERT_TRIGGER = EXAMPLE / "alert-trigger"
sys.path.insert(0, str(ALERT_TRIGGER))

from evaluation import build_report, trace_ids  # noqa: E402


def run_live(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from holmes_client import HolmesClient
    from tasks import PermanentTaskError, RetryableTaskError

    base_url = os.environ.get("HOLMES_API_URL", "").strip()
    api_key = os.environ.get("HOLMES_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("live mode requires HOLMES_API_URL and HOLMES_API_KEY")

    client = HolmesClient(base_url=base_url, api_key=api_key)
    results = {}
    for case in cases:
        try:
            results[case["id"]] = client.investigate(
                {
                    "alert_name": case["alert"],
                    "trace_ids": trace_ids(case),
                    "summary": {
                        "service": case["service"],
                        "symptom": case["symptom"],
                        "fixture_source": "synthetic_known_root_cause_case",
                    },
                }
            )
        except (PermanentTaskError, RetryableTaskError, ValueError) as exc:
            # Store only a stable safe code, never upstream details.
            code = getattr(exc, "code", "evaluation_request_failed")
            results[case["id"]] = {"evidence": [], "evidence_status": "unavailable", "error_code": str(code)[:64]}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--output", type=Path, help="Report destination (defaults to stdout)")
    parser.add_argument("--confirm-live", action="store_true", help="Confirm that live mode may make 20 Holmes/model requests")
    args = parser.parse_args()

    cases = json.loads((Path(__file__).with_name("known_root_causes.json")).read_text(encoding="utf-8"))
    if args.mode == "live" and not args.confirm_live:
        parser.error("live mode sends up to 20 requests; pass --confirm-live to proceed")
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
