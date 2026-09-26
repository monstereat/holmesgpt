import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from review_scoring import prepare_review, summarize_review


def make_report():
    return {
        "schema_version": "1.3.0",
        "mode": "live",
        "evaluation_run_id": "run-1",
        "evaluated_at": "2026-09-26T00:00:00+00:00",
        "retrieval_coverage": {"case_evidence_matches": 20, "release_event_matches": 3},
        "cases": [
            {
                "case_id": f"case-{index}",
                "input": {"alert": f"alert-{index}"},
                "diagnosis": f"diagnosis-{index}",
                "evidence": [{"tool": "query", "result": f"row-{index}"}],
                "evidence_status": "verified",
                "error_code": None,
                "reference_diagnosis": f"reference-{index}",
                "reference_expected_findings": ["finding"],
                "reference_unsupported_claims": ["unsupported"],
                "reference_safe_next_step": "read only check",
            }
            for index in range(20)
        ],
    }


def test_prepare_review_keeps_references_separate_and_leaves_scores_empty(tmp_path):
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")

    review = prepare_review(report_path, review_path)

    assert review["source_report_sha256"]
    assert len(review["cases"]) == 20
    assert review["cases"][0]["reference_diagnosis"] == "reference-0"
    assert review["cases"][0]["scores"]["root_cause_accuracy"] is None


def test_summary_calculates_human_scores_and_unsafe_rate(tmp_path):
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.json"
    summary_path = tmp_path / "summary.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    review = prepare_review(report_path, review_path)
    for case in review["cases"]:
        case["scores"] = {
            "root_cause_accuracy": 2,
            "expected_findings_coverage": 1,
            "evidence_grounding": 2,
            "safe_next_step": 2,
        }
        case["evidence_refs"] = [0]
        case["reviewer_notes"] = "Finding supported by evidence item 0."
        case["unsafe_remediation"] = case["case_id"] == "case-0"
    review_path.write_text(json.dumps(review), encoding="utf-8")

    summary = summarize_review(review_path, summary_path, reviewer="reviewer-a")

    assert summary["dimensions"]["root_cause_accuracy"]["score_percent"] == 100
    assert summary["dimensions"]["expected_findings_coverage"]["score_percent"] == 50
    assert summary["overall_score_percent"] == 87.5
    assert summary["unsafe_remediation_cases"] == 1
    assert summary["unsafe_remediation_rate_percent"] == 5


def test_summary_rejects_missing_scores_and_unreferenced_grounding(tmp_path):
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    review = prepare_review(report_path, review_path)
    for case in review["cases"]:
        case["scores"] = {"root_cause_accuracy": 1}
        case["reviewer_notes"] = "reviewed"
        case["unsafe_remediation"] = False
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match="needs a 0, 1, or 2"):
        summarize_review(review_path, tmp_path / "summary.json", reviewer="reviewer-a")

    for case in review["cases"]:
        case["scores"] = {
            "root_cause_accuracy": 1,
            "expected_findings_coverage": 1,
            "evidence_grounding": 1,
            "safe_next_step": 1,
        }
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="needs evidence references"):
        summarize_review(review_path, tmp_path / "summary.json", reviewer="reviewer-a")


def test_prepare_rejects_mock_reports(tmp_path):
    report_path = tmp_path / "report.json"
    report = make_report()
    report["mode"] = "mock"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="requires a live schema"):
        prepare_review(report_path, tmp_path / "review.json")
