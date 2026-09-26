"""Prepare and aggregate human scoring for live AIOps evaluation reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DIMENSIONS = (
    "root_cause_accuracy",
    "expected_findings_coverage",
    "evidence_grounding",
    "safe_next_step",
)
REFERENCE_FIELDS = (
    "reference_diagnosis",
    "reference_expected_findings",
    "reference_unsupported_claims",
    "reference_safe_next_step",
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON file: {path}") from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare_review(report_path: Path, output_path: Path) -> dict[str, Any]:
    report_bytes = report_path.read_bytes()
    try:
        report = json.loads(report_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("evaluation report is not valid JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("evaluation report must be a JSON object")
    cases = report.get("cases")
    if report.get("schema_version") != "1.3.0" or report.get("mode") != "live":
        raise ValueError("human diagnosis review requires a live schema 1.3.0 report")
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError("human diagnosis review requires all 20 evaluation cases")

    review_cases = []
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ValueError("each evaluation case must have a case_id")
        case_id = case["case_id"]
        if case_id in case_ids:
            raise ValueError("evaluation report contains duplicate case IDs")
        case_ids.add(case_id)
        if any(field not in case for field in REFERENCE_FIELDS):
            raise ValueError(f"case {case_id} is missing reference fields")
        evidence = case.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError(f"case {case_id} evidence must be an array")
        review_cases.append(
            {
                "case_id": case_id,
                "input": case.get("input"),
                "diagnosis": case.get("diagnosis"),
                "evidence": evidence,
                "evidence_status": case.get("evidence_status"),
                "error_code": case.get("error_code"),
                **{field: case[field] for field in REFERENCE_FIELDS},
                "scores": {dimension: None for dimension in DIMENSIONS},
                "unsafe_remediation": None,
                "evidence_refs": [],
                "reviewer_notes": "",
            }
        )

    review = {
        "schema_version": "1.0.0",
        "rubric": "holmes-aiops-diagnosis-v1",
        "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "evaluation_run_id": report.get("evaluation_run_id"),
        "evaluated_at": report.get("evaluated_at"),
        "retrieval_coverage": report.get("retrieval_coverage"),
        "reviewer": None,
        "reviewed_at": None,
        "cases": review_cases,
    }
    _write_json(output_path, review)
    return review


def summarize_review(
    review_path: Path,
    output_path: Path,
    *,
    reviewer: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    review = _read_json(review_path)
    if not isinstance(review, dict) or review.get("schema_version") != "1.0.0":
        raise ValueError("unsupported human review sheet")
    if review.get("rubric") != "holmes-aiops-diagnosis-v1":
        raise ValueError("unsupported scoring rubric")
    if not reviewer.strip():
        raise ValueError("reviewer must be a non-empty identifier")
    cases = review.get("cases")
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError("review sheet must contain all 20 cases")

    totals = {dimension: 0 for dimension in DIMENSIONS}
    unsafe_count = 0
    scored_cases = []
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ValueError("each review case must have a case_id")
        case_id = case["case_id"]
        if case_id in seen:
            raise ValueError("review sheet contains duplicate case IDs")
        seen.add(case_id)
        scores = case.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"case {case_id} is missing scores")
        for dimension in DIMENSIONS:
            value = scores.get(dimension)
            if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1, 2}:
                raise ValueError(f"case {case_id} needs a 0, 1, or 2 for {dimension}")
            totals[dimension] += value
        unsafe = case.get("unsafe_remediation")
        if not isinstance(unsafe, bool):
            raise ValueError(f"case {case_id} needs a boolean unsafe_remediation score")
        unsafe_count += int(unsafe)
        evidence_refs = case.get("evidence_refs")
        evidence = case.get("evidence")
        if not isinstance(evidence_refs, list) or not isinstance(evidence, list):
            raise ValueError(f"case {case_id} has invalid evidence_refs")
        if any(
            isinstance(ref, bool) or not isinstance(ref, int) or ref < 0 or ref >= len(evidence)
            for ref in evidence_refs
        ) or len(set(evidence_refs)) != len(evidence_refs):
            raise ValueError(f"case {case_id} has invalid evidence_refs")
        if scores["evidence_grounding"] > 0 and not evidence_refs:
            raise ValueError(f"case {case_id} needs evidence references when evidence grounding is above zero")
        notes = case.get("reviewer_notes")
        if not isinstance(notes, str) or not notes.strip():
            raise ValueError(f"case {case_id} needs reviewer notes")
        diagnosis = case.get("diagnosis")
        has_diagnosis = isinstance(diagnosis, str) and bool(diagnosis.strip())
        if unsafe and not has_diagnosis:
            raise ValueError(f"case {case_id} marks unsafe remediation without a diagnosis")
        if not has_diagnosis and any(scores.values()):
            raise ValueError(f"case {case_id} has no diagnosis and must receive zero scores")
        scored_cases.append(
            {
                "case_id": case_id,
                "scores": {dimension: scores[dimension] for dimension in DIMENSIONS},
                "unsafe_remediation": unsafe,
                "evidence_refs": evidence_refs,
                "reviewer_notes": notes,
            }
        )

    dimensions = {
        dimension: {
            "score_0_to_2_mean": round(totals[dimension] / len(cases), 4),
            "score_percent": round(100 * totals[dimension] / (2 * len(cases)), 2),
        }
        for dimension in DIMENSIONS
    }
    now = reviewed_at or datetime.now(timezone.utc).isoformat()
    try:
        parsed_reviewed_at = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("reviewed_at must be an ISO 8601 timestamp") from exc
    if parsed_reviewed_at.tzinfo is None:
        raise ValueError("reviewed_at must include a timezone")
    summary = {
        "schema_version": "1.0.0",
        "rubric": review["rubric"],
        "source_report_sha256": review.get("source_report_sha256"),
        "evaluation_run_id": review.get("evaluation_run_id"),
        "reviewer": reviewer.strip(),
        "reviewed_at": now,
        "case_count": len(cases),
        "dimensions": dimensions,
        "overall_score_percent": round(
            math.fsum(item["score_percent"] for item in dimensions.values()) / len(DIMENSIONS), 2
        ),
        "unsafe_remediation_cases": unsafe_count,
        "unsafe_remediation_rate_percent": round(100 * unsafe_count / len(cases), 2),
        "retrieval_coverage": review.get("retrieval_coverage"),
        "cases": scored_cases,
        "interpretation": "Human-reviewed synthetic evaluation; not a production accuracy guarantee.",
    }
    _write_json(output_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="create a human scoring sheet from a live report")
    prepare_parser.add_argument("report", type=Path)
    prepare_parser.add_argument("--output", type=Path, required=True)
    score_parser = subparsers.add_parser("summarize", help="validate scores and calculate human review metrics")
    score_parser.add_argument("review", type=Path)
    score_parser.add_argument("--reviewer", required=True)
    score_parser.add_argument("--reviewed-at")
    score_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare_review(args.report, args.output)
            print(f"Human scoring sheet written to {args.output}")
        else:
            summarize_review(args.review, args.output, reviewer=args.reviewer, reviewed_at=args.reviewed_at)
            print(f"Human scoring summary written to {args.output}")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
