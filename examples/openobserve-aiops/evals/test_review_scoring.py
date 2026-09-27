import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from review_scoring import compare_summaries, prepare_review, summarize_review


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


def test_prepare_review_blinds_references_and_leaves_scores_empty(tmp_path):
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.json"
    answer_key_path = tmp_path / "answer-key.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")

    review = prepare_review(report_path, review_path, answer_key_path)
    answer_key = json.loads(answer_key_path.read_text(encoding="utf-8"))

    assert review["source_report_sha256"]
    assert len(review["cases"]) == 20
    assert all(not any(field.startswith("reference_") for field in case) for case in review["cases"])
    assert answer_key["cases"][0]["reference_diagnosis"] == "reference-0"
    assert review["cases"][0]["scores"]["root_cause_accuracy"] is None
    assert review_path.stat().st_mode & 0o777 == 0o600
    assert answer_key_path.stat().st_mode & 0o777 == 0o600


def test_summary_calculates_human_scores_and_unsafe_rate(tmp_path):
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.json"
    answer_key_path = tmp_path / "answer-key.json"
    summary_path = tmp_path / "summary.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    review = prepare_review(report_path, review_path, answer_key_path)
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
    answer_key_path = tmp_path / "answer-key.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    review = prepare_review(report_path, review_path, answer_key_path)
    for case in review["cases"]:
        case["scores"] = {"root_cause_accuracy": 1}
        case["reviewer_notes"] = "reviewed"
        case["unsafe_remediation"] = False
    review_path.write_text(json.dumps(review), encoding="utf-8")

    review["cases"][0]["reference_diagnosis"] = "leaked answer"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="must not contain reference answers"):
        summarize_review(review_path, tmp_path / "leaked-summary.json", reviewer="reviewer-a")

    del review["cases"][0]["reference_diagnosis"]
    review["metadata"] = {"nested": [{"reference_expected_findings": ["leaked"]}]}
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="must not contain reference answers"):
        summarize_review(review_path, tmp_path / "nested-leaked-summary.json", reviewer="reviewer-a")

    del review["metadata"]
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
        prepare_review(report_path, tmp_path / "review.json", tmp_path / "answer-key.json")


def test_prepare_refuses_colliding_or_overwriting_outputs(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    same_path = tmp_path / "same.json"

    with pytest.raises(ValueError, match="different paths"):
        prepare_review(report_path, same_path, same_path)

    existing_review = tmp_path / "review.json"
    existing_key = tmp_path / "answer-key.json"
    existing_review.write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        prepare_review(report_path, existing_review, existing_key)
    assert existing_review.read_text(encoding="utf-8") == "preserve"


def make_scored_summary(reviewer, report_sha="a" * 64, score=2, unsafe=False):
    return {
        "schema_version": "2.0.0",
        "rubric": "holmes-aiops-diagnosis-v2-blind",
        "source_report_sha256": report_sha,
        "evaluation_run_id": "run-1",
        "reviewer": reviewer,
        "case_count": 20,
        "cases": [
            {
                "case_id": f"case-{index}",
                "scores": {dimension: score for dimension in (
                    "root_cause_accuracy",
                    "expected_findings_coverage",
                    "evidence_grounding",
                    "safe_next_step",
                )},
                "unsafe_remediation": unsafe and index == 0,
            }
            for index in range(20)
        ],
    }


def test_compare_reports_reviewer_agreement_and_explicit_adjudication(tmp_path):
    first_path = tmp_path / "reviewer-a.json"
    second_path = tmp_path / "reviewer-b.json"
    first_path.write_text(json.dumps(make_scored_summary("reviewer-a")), encoding="utf-8")
    second = make_scored_summary("reviewer-b", score=1, unsafe=True)
    second["cases"][0]["scores"]["root_cause_accuracy"] = 0
    second_path.write_text(json.dumps(second), encoding="utf-8")

    comparison = compare_summaries(first_path, second_path, tmp_path / "comparison.json")

    assert comparison["dimension_agreement"]["root_cause_accuracy"]["exact_matches"] == 0
    assert comparison["dimension_agreement"]["expected_findings_coverage"]["mean_absolute_difference_0_to_2"] == 1
    assert comparison["unsafe_remediation_agreement"]["disagreement_case_ids"] == ["case-0"]
    assert comparison["adjudication_required"] is True
    assert len(comparison["disagreements"]) == 20


def test_compare_rejects_same_reviewer_and_different_source_report(tmp_path):
    first_path = tmp_path / "reviewer-a.json"
    second_path = tmp_path / "reviewer-b.json"
    first_path.write_text(json.dumps(make_scored_summary("reviewer-a")), encoding="utf-8")
    second_path.write_text(json.dumps(make_scored_summary("reviewer-a", report_sha="b" * 64)), encoding="utf-8")

    with pytest.raises(ValueError, match="different reviewer identifiers"):
        compare_summaries(first_path, second_path, tmp_path / "comparison.json")

    second_path.write_text(json.dumps(make_scored_summary("reviewer-b", report_sha="b" * 64)), encoding="utf-8")
    with pytest.raises(ValueError, match="different source reports"):
        compare_summaries(first_path, second_path, tmp_path / "comparison.json")
