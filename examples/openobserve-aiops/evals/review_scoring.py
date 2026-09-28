"""Prepare and aggregate human scoring for live AIOps evaluation reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import string
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
REVIEW_CONTEXT_FIELDS = (
    "case_id",
    "input",
    "diagnosis",
    "evidence",
    "evidence_status",
    "error_code",
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON file: {path}") from exc


def _write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _contains_reference_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (isinstance(key, str) and key.startswith("reference_")) or _contains_reference_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_reference_field(item) for item in value)
    return False


def _review_context_sha256(review: dict[str, Any]) -> str:
    material = {
        "schema_version": review.get("schema_version"),
        "rubric": review.get("rubric"),
        "source_report_sha256": review.get("source_report_sha256"),
        "evaluation_run_id": review.get("evaluation_run_id"),
        "evaluated_at": review.get("evaluated_at"),
        "retrieval_coverage": review.get("retrieval_coverage"),
        "cases": [
            {field: case.get(field) for field in REVIEW_CONTEXT_FIELDS}
            for case in review.get("cases", [])
            if isinstance(case, dict)
        ],
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_review(report_path: Path, output_path: Path, answer_key_path: Path) -> dict[str, Any]:
    resolved_paths = {path.resolve() for path in (report_path, output_path, answer_key_path)}
    if len(resolved_paths) != 3:
        raise ValueError("report, review sheet, and answer key must use different paths")
    if output_path.exists() or answer_key_path.exists():
        raise ValueError("refusing to overwrite an existing review sheet or answer key")
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
    reference_cases = []
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
        reference_cases.append({"case_id": case_id, **{field: case[field] for field in REFERENCE_FIELDS}})
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
                "scores": {dimension: None for dimension in DIMENSIONS},
                "unsafe_remediation": None,
                "evidence_refs": [],
                "reviewer_notes": "",
            }
        )

    review = {
        "schema_version": "2.1.0",
        "rubric": "holmes-aiops-diagnosis-v2-blind",
        "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "evaluation_run_id": report.get("evaluation_run_id"),
        "evaluated_at": report.get("evaluated_at"),
        "retrieval_coverage": report.get("retrieval_coverage"),
        "reviewer": None,
        "reviewed_at": None,
        "cases": review_cases,
    }
    review["review_context_sha256"] = _review_context_sha256(review)
    answer_key = {
        "schema_version": "1.0.0",
        "rubric": review["rubric"],
        "source_report_sha256": review["source_report_sha256"],
        "evaluation_run_id": review["evaluation_run_id"],
        "review_context_sha256": review["review_context_sha256"],
        "cases": reference_cases,
    }
    _write_json(output_path, review, exclusive=True)
    _write_json(answer_key_path, answer_key, exclusive=True)
    return review


def summarize_review(
    review_path: Path,
    output_path: Path,
    *,
    reviewer: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    review = _read_json(review_path)
    if not isinstance(review, dict) or review.get("schema_version") != "2.1.0":
        raise ValueError("unsupported human review sheet")
    if review.get("rubric") != "holmes-aiops-diagnosis-v2-blind":
        raise ValueError("unsupported scoring rubric")
    if _contains_reference_field(review):
        raise ValueError("review sheet must not contain reference answers")
    cases = review.get("cases")
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError("review sheet must contain all 20 cases")
    context_sha256 = review.get("review_context_sha256")
    if (
        not isinstance(context_sha256, str)
        or len(context_sha256) != 64
        or any(character not in string.hexdigits for character in context_sha256)
        or _review_context_sha256(review) != context_sha256
    ):
        raise ValueError("review context does not match its SHA-256")
    if not reviewer.strip():
        raise ValueError("reviewer must be a non-empty identifier")

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
        if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
            raise ValueError(f"case {case_id} must contain exactly the rubric dimensions")
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
        "schema_version": "2.0.0",
        "rubric": review["rubric"],
        "source_report_sha256": review.get("source_report_sha256"),
        "review_context_sha256": context_sha256,
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
    _write_json(output_path, summary, exclusive=True)
    return summary


def compare_summaries(
    first_path: Path,
    second_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    first = _read_json(first_path)
    second = _read_json(second_path)
    for summary in (first, second):
        if not isinstance(summary, dict) or summary.get("schema_version") != "2.0.0":
            raise ValueError("unsupported human review summary")
        if summary.get("rubric") != "holmes-aiops-diagnosis-v2-blind":
            raise ValueError("unsupported scoring rubric")
        if not isinstance(summary.get("reviewer"), str) or not summary["reviewer"].strip():
            raise ValueError("review summary needs a reviewer identifier")
        report_sha = summary.get("source_report_sha256")
        if (
            not isinstance(report_sha, str)
            or len(report_sha) != 64
            or any(character not in string.hexdigits for character in report_sha)
        ):
            raise ValueError("review summary needs a source report SHA-256")
        if not isinstance(summary.get("evaluation_run_id"), str) or not summary["evaluation_run_id"]:
            raise ValueError("review summary needs an evaluation run ID")
        if summary.get("case_count") != 20 or not isinstance(summary.get("cases"), list):
            raise ValueError("review summary must contain all 20 cases")

    if first["reviewer"].strip().casefold() == second["reviewer"].strip().casefold():
        raise ValueError("independent reviews must have different reviewer identifiers")
    if first["source_report_sha256"] != second["source_report_sha256"]:
        raise ValueError("review summaries refer to different source reports")
    if first["evaluation_run_id"] != second["evaluation_run_id"]:
        raise ValueError("review summaries refer to different evaluation runs")
    first_context = first.get("review_context_sha256")
    second_context = second.get("review_context_sha256")
    if (
        not isinstance(first_context, str)
        or len(first_context) != 64
        or any(character not in string.hexdigits for character in first_context)
    ):
        raise ValueError("review summaries need a valid review context SHA-256")
    if first_context != second_context:
        raise ValueError("review summaries refer to different review contexts")

    def index_cases(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for case in summary["cases"]:
            if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
                raise ValueError("review summary contains an invalid case")
            case_id = case["case_id"]
            if case_id in indexed:
                raise ValueError("review summary contains duplicate case IDs")
            scores = case.get("scores")
            if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
                raise ValueError(f"case {case_id} must contain exactly the rubric dimensions")
            for dimension in DIMENSIONS:
                value = scores.get(dimension)
                if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1, 2}:
                    raise ValueError(f"case {case_id} needs a 0, 1, or 2 for {dimension}")
            if not isinstance(case.get("unsafe_remediation"), bool):
                raise ValueError(f"case {case_id} needs a boolean unsafe_remediation score")
            indexed[case_id] = case
        if len(indexed) != 20:
            raise ValueError("review summary must contain 20 unique case IDs")
        return indexed

    first_cases = index_cases(first)
    second_cases = index_cases(second)
    if first_cases.keys() != second_cases.keys():
        raise ValueError("review summaries contain different case IDs")

    dimension_comparison = {}
    for dimension in DIMENSIONS:
        differences = [
            abs(first_cases[case_id]["scores"][dimension] - second_cases[case_id]["scores"][dimension])
            for case_id in first_cases
        ]
        exact_matches = sum(difference == 0 for difference in differences)
        dimension_comparison[dimension] = {
            "exact_matches": exact_matches,
            "exact_match_percent": round(100 * exact_matches / len(differences), 2),
            "mean_absolute_difference_0_to_2": round(math.fsum(differences) / len(differences), 4),
        }

    unsafe_disagreements = [
        case_id
        for case_id in first_cases
        if first_cases[case_id]["unsafe_remediation"] != second_cases[case_id]["unsafe_remediation"]
    ]
    case_disagreements = []
    for case_id in first_cases:
        first_case = first_cases[case_id]
        second_case = second_cases[case_id]
        differing_dimensions = {
            dimension: {
                first["reviewer"]: first_case["scores"][dimension],
                second["reviewer"]: second_case["scores"][dimension],
            }
            for dimension in DIMENSIONS
            if first_case["scores"][dimension] != second_case["scores"][dimension]
        }
        unsafe_differs = first_case["unsafe_remediation"] != second_case["unsafe_remediation"]
        if differing_dimensions or unsafe_differs:
            case_disagreements.append(
                {
                    "case_id": case_id,
                    "dimension_scores": differing_dimensions,
                    "unsafe_remediation": {
                        first["reviewer"]: first_case["unsafe_remediation"],
                        second["reviewer"]: second_case["unsafe_remediation"],
                    }
                    if unsafe_differs
                    else None,
                    "summary_refs": {
                        first["reviewer"]: str(first_path),
                        second["reviewer"]: str(second_path),
                    },
                }
            )

    comparison = {
        "schema_version": "2.0.0",
        "rubric": first["rubric"],
        "source_report_sha256": first["source_report_sha256"],
        "review_context_sha256": first_context,
        "evaluation_run_id": first["evaluation_run_id"],
        "case_count": 20,
        "reviewers": [first["reviewer"], second["reviewer"]],
        "dimension_agreement": dimension_comparison,
        "unsafe_remediation_agreement": {
            "exact_matches": 20 - len(unsafe_disagreements),
            "exact_match_percent": round(100 * (20 - len(unsafe_disagreements)) / 20, 2),
            "disagreement_case_ids": unsafe_disagreements,
        },
        "disagreement_case_count": len(case_disagreements),
        "disagreements": case_disagreements,
        "adjudication_required": bool(case_disagreements),
        "interpretation": (
            "Reviewer agreement only; do not average scores or cite a final diagnosis-quality result "
            "until disagreements are adjudicated."
        ),
    }
    _write_json(output_path, comparison, exclusive=True)
    return comparison


def adjudicate_summaries(
    first_path: Path,
    second_path: Path,
    decisions_path: Path,
    output_path: Path,
    *,
    adjudicator: str,
    adjudicated_at: str | None = None,
) -> dict[str, Any]:
    first = _read_json(first_path)
    second = _read_json(second_path)
    for summary in (first, second):
        if not isinstance(summary, dict) or summary.get("schema_version") != "2.0.0":
            raise ValueError("unsupported human review summary")
        if summary.get("rubric") != "holmes-aiops-diagnosis-v2-blind":
            raise ValueError("unsupported scoring rubric")
        if not isinstance(summary.get("reviewer"), str) or not summary["reviewer"].strip():
            raise ValueError("review summary needs a reviewer identifier")
        report_sha = summary.get("source_report_sha256")
        if (
            not isinstance(report_sha, str)
            or len(report_sha) != 64
            or any(character not in string.hexdigits for character in report_sha)
        ):
            raise ValueError("review summary needs a source report SHA-256")
        if not isinstance(summary.get("evaluation_run_id"), str) or not summary["evaluation_run_id"]:
            raise ValueError("review summary needs an evaluation run ID")
        if summary.get("case_count") != 20 or not isinstance(summary.get("cases"), list):
            raise ValueError("review summary must contain all 20 cases")
    if first["reviewer"].strip().casefold() == second["reviewer"].strip().casefold():
        raise ValueError("independent reviews must have different reviewer identifiers")
    if first["source_report_sha256"] != second["source_report_sha256"]:
        raise ValueError("review summaries refer to different source reports")
    if first["evaluation_run_id"] != second["evaluation_run_id"]:
        raise ValueError("review summaries refer to different evaluation runs")
    first_context = first.get("review_context_sha256")
    second_context = second.get("review_context_sha256")
    if (
        not isinstance(first_context, str)
        or len(first_context) != 64
        or any(character not in string.hexdigits for character in first_context)
    ):
        raise ValueError("review summaries need a valid review context SHA-256")
    if first_context != second_context:
        raise ValueError("review summaries refer to different review contexts")
    if not adjudicator.strip() or adjudicator.strip().casefold() in {
        first["reviewer"].strip().casefold(),
        second["reviewer"].strip().casefold(),
    }:
        raise ValueError("adjudicator must be identified and independent from both reviewers")

    def index_cases(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for case in summary["cases"]:
            if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
                raise ValueError("review summary contains an invalid case")
            case_id = case["case_id"]
            if case_id in indexed:
                raise ValueError("review summary contains duplicate case IDs")
            scores = case.get("scores")
            if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
                raise ValueError(f"case {case_id} must contain exactly the rubric dimensions")
            for dimension in DIMENSIONS:
                value = scores.get(dimension)
                if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1, 2}:
                    raise ValueError(f"case {case_id} needs a 0, 1, or 2 for {dimension}")
            if not isinstance(case.get("unsafe_remediation"), bool):
                raise ValueError(f"case {case_id} needs a boolean unsafe_remediation score")
            indexed[case_id] = case
        if len(indexed) != 20:
            raise ValueError("review summary must contain 20 unique case IDs")
        return indexed

    first_cases = index_cases(first)
    second_cases = index_cases(second)
    if first_cases.keys() != second_cases.keys():
        raise ValueError("review summaries contain different case IDs")

    decisions = _read_json(decisions_path)
    if not isinstance(decisions, dict) or not isinstance(decisions.get("cases"), list):
        raise ValueError("adjudication decisions must contain a cases array")
    expected_decision_fields = {
        "schema_version",
        "source_report_sha256",
        "review_context_sha256",
        "evaluation_run_id",
        "cases",
    }
    if set(decisions) != expected_decision_fields:
        raise ValueError("adjudication decisions contain unsupported fields")
    if decisions.get("schema_version") != "1.0.0":
        raise ValueError("unsupported adjudication decision schema")
    if decisions.get("source_report_sha256") != first["source_report_sha256"]:
        raise ValueError("adjudication decisions refer to a different source report")
    if decisions.get("evaluation_run_id") != first["evaluation_run_id"]:
        raise ValueError("adjudication decisions refer to a different evaluation run")
    if decisions.get("review_context_sha256") != first_context:
        raise ValueError("adjudication decisions refer to a different review context")
    decision_by_case: dict[str, dict[str, Any]] = {}
    for decision in decisions["cases"]:
        if not isinstance(decision, dict) or not isinstance(decision.get("case_id"), str):
            raise ValueError("each adjudication decision must have a case_id")
        if set(decision) - {"case_id", "scores", "unsafe_remediation", "rationale"}:
            raise ValueError("adjudication decision contains unsupported fields")
        case_id = decision["case_id"]
        if case_id in decision_by_case:
            raise ValueError("adjudication decisions contain duplicate case IDs")
        decision_by_case[case_id] = decision

    totals = {dimension: 0 for dimension in DIMENSIONS}
    final_cases = []
    disagreement_ids = set()
    for case_id in first_cases:
        first_case, second_case = first_cases[case_id], second_cases[case_id]
        differing = {
            dimension
            for dimension in DIMENSIONS
            if first_case["scores"][dimension] != second_case["scores"][dimension]
        }
        unsafe_differs = first_case["unsafe_remediation"] != second_case["unsafe_remediation"]
        decision = decision_by_case.get(case_id)
        if differing or unsafe_differs:
            disagreement_ids.add(case_id)
            if not decision:
                raise ValueError(f"case {case_id} needs an adjudication decision")
            rationale = decision.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError(f"case {case_id} needs an adjudication rationale")
            adjudicated_scores = decision.get("scores")
            if not isinstance(adjudicated_scores, dict) or set(adjudicated_scores) != differing:
                raise ValueError(f"case {case_id} must adjudicate exactly the differing dimensions")
            scores = {}
            for dimension in DIMENSIONS:
                if dimension in differing:
                    value = adjudicated_scores[dimension]
                    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1, 2}:
                        raise ValueError(f"case {case_id} needs a 0, 1, or 2 for {dimension}")
                    scores[dimension] = value
                else:
                    scores[dimension] = first_case["scores"][dimension]
            unsafe_value = decision.get("unsafe_remediation")
            if unsafe_differs and not isinstance(unsafe_value, bool):
                raise ValueError(f"case {case_id} needs an adjudicated unsafe_remediation value")
            if not unsafe_differs and "unsafe_remediation" in decision:
                raise ValueError(f"case {case_id} must not override an agreed unsafe_remediation value")
            unsafe = unsafe_value if unsafe_differs else first_case["unsafe_remediation"]
            decision_result = {"rationale": rationale.strip()}
        else:
            if decision:
                raise ValueError(f"case {case_id} does not need adjudication")
            scores = dict(first_case["scores"])
            unsafe = first_case["unsafe_remediation"]
            decision_result = None

        for dimension in DIMENSIONS:
            totals[dimension] += scores[dimension]
        final_cases.append(
            {
                "case_id": case_id,
                "reviewer_scores": {
                    first["reviewer"]: dict(first_case["scores"]),
                    second["reviewer"]: dict(second_case["scores"]),
                },
                "reviewer_unsafe_remediation": {
                    first["reviewer"]: first_case["unsafe_remediation"],
                    second["reviewer"]: second_case["unsafe_remediation"],
                },
                "scores": scores,
                "unsafe_remediation": unsafe,
                "adjudication": decision_result,
            }
        )

    extra_decisions = set(decision_by_case) - disagreement_ids
    if extra_decisions:
        extra_ids = ", ".join(sorted(extra_decisions))
        raise ValueError(f"adjudication supplied for cases without disagreement: {extra_ids}")
    dimensions = {
        dimension: {
            "score_0_to_2_mean": round(totals[dimension] / len(final_cases), 4),
            "score_percent": round(100 * totals[dimension] / (2 * len(final_cases)), 2),
        }
        for dimension in DIMENSIONS
    }
    unsafe_count = sum(case["unsafe_remediation"] for case in final_cases)
    now = adjudicated_at or datetime.now(timezone.utc).isoformat()
    try:
        parsed_adjudicated_at = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("adjudicated_at must be an ISO 8601 timestamp") from exc
    if parsed_adjudicated_at.tzinfo is None:
        raise ValueError("adjudicated_at must include a timezone")
    result = {
        "schema_version": "1.0.0",
        "rubric": first["rubric"],
        "source_report_sha256": first["source_report_sha256"],
        "review_context_sha256": first_context,
        "evaluation_run_id": first["evaluation_run_id"],
        "reviewers": [first["reviewer"], second["reviewer"]],
        "review_summary_sha256": {
            first["reviewer"]: hashlib.sha256(first_path.read_bytes()).hexdigest(),
            second["reviewer"]: hashlib.sha256(second_path.read_bytes()).hexdigest(),
        },
        "adjudication_decisions_sha256": hashlib.sha256(decisions_path.read_bytes()).hexdigest(),
        "adjudicator": adjudicator.strip(),
        "adjudicated_at": now,
        "case_count": len(final_cases),
        "adjudicated_case_count": len(disagreement_ids),
        "unsafe_remediation_cases": unsafe_count,
        "unsafe_remediation_rate_percent": round(100 * unsafe_count / len(final_cases), 2),
        "dimensions": dimensions,
        "overall_score_percent": round(
            math.fsum(item["score_percent"] for item in dimensions.values()) / len(DIMENSIONS), 2
        ),
        "cases": final_cases,
        "interpretation": "Human-adjudicated synthetic evaluation; not a production accuracy guarantee.",
    }
    _write_json(output_path, result, exclusive=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="create a blind review sheet and separate answer key")
    prepare_parser.add_argument("report", type=Path)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--answer-key", type=Path, required=True)
    score_parser = subparsers.add_parser("summarize", help="validate scores and calculate human review metrics")
    score_parser.add_argument("review", type=Path)
    score_parser.add_argument("--reviewer", required=True)
    score_parser.add_argument("--reviewed-at")
    score_parser.add_argument("--output", type=Path, required=True)
    compare_parser = subparsers.add_parser("compare", help="compare two independently scored review summaries")
    compare_parser.add_argument("first", type=Path)
    compare_parser.add_argument("second", type=Path)
    compare_parser.add_argument("--output", type=Path, required=True)
    adjudicate_parser = subparsers.add_parser(
        "adjudicate", help="calculate final scores after explicit human adjudication"
    )
    adjudicate_parser.add_argument("first", type=Path)
    adjudicate_parser.add_argument("second", type=Path)
    adjudicate_parser.add_argument("--decisions", type=Path, required=True)
    adjudicate_parser.add_argument("--adjudicator", required=True)
    adjudicate_parser.add_argument("--adjudicated-at")
    adjudicate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare_review(args.report, args.output, args.answer_key)
            print(f"Blind human scoring sheet written to {args.output}")
            print(f"Keep the answer key private until both reviews are complete: {args.answer_key}")
        elif args.command == "summarize":
            summarize_review(args.review, args.output, reviewer=args.reviewer, reviewed_at=args.reviewed_at)
            print(f"Human scoring summary written to {args.output}")
        elif args.command == "compare":
            compare_summaries(args.first, args.second, args.output)
            print(f"Independent review comparison written to {args.output}")
        else:
            adjudicate_summaries(
                args.first,
                args.second,
                args.decisions,
                args.output,
                adjudicator=args.adjudicator,
                adjudicated_at=args.adjudicated_at,
            )
            print(f"Human-adjudicated score report written to {args.output}")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
