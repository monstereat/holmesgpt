import pytest

from evaluation import CLUSTER_BY_CASE, CLUSTER_TAXONOMY, build_case_report, build_report, trace_ids


def cases():
    return [
        {
            "id": case_id,
            "alert": "SyntheticAlert",
            "service": "order-service",
            "symptom": "Synthetic fixture symptom",
            "evidence": [f"Trace {index + 1:032x}: synthetic fixture evidence"],
            "root_cause": f"Reference diagnosis for {case_id}",
            "expected_findings": ["Synthetic finding"],
            "unsupported_claims": ["Synthetic unsupported claim"],
            "safe_next_step": "Review with the service owner.",
        }
        for index, case_id in enumerate(CLUSTER_BY_CASE)
    ]


def test_corpus_has_a_curated_cluster_for_every_case_and_extracts_trace_ids():
    corpus = cases()
    assert len(CLUSTER_BY_CASE) == len(corpus) == 20
    assert {case["id"] for case in corpus} == set(CLUSTER_BY_CASE)
    assert len(set(CLUSTER_BY_CASE.values())) == len(corpus)
    assert CLUSTER_TAXONOMY == "curated_fixture_taxonomy"
    assert trace_ids(corpus[0]) == ["00000000000000000000000000000001"]


def test_mock_report_preserves_fixture_provenance_without_claiming_model_results():
    report = build_report(cases(), mode="mock")
    assert report["case_source"] == "synthetic_fixture"
    assert report["evaluation_run_id"] is None
    assert report["seeded_record_count"] is None
    assert report["scoring"] == "not_scored"
    assert report["counts"] == {"total": 20, "live_errors": 0}
    assert all(case["diagnosis"] is None for case in report["cases"])
    assert all(case["evidence_source"] == "synthetic_fixture" for case in report["cases"])
    assert all(case["cluster_source"] == CLUSTER_TAXONOMY for case in report["cases"])


def test_live_report_separates_live_retrieval_from_synthetic_case_source():
    case = cases()[0]
    report = build_case_report(
        case,
        mode="live",
        live_result={
            "analysis": "Observed result",
            "evidence": [{"tool_name": "openobserve_find_trace", "status": "success"}],
            "evidence_status": "verified",
            "evaluation_run_id": "run-123",
            "evaluation_trace_id": "a" * 32,
            "seeded_record_count": 40,
        },
    )
    assert report["source"] == "known_root_causes.json#order-inventory-negative-stock"
    assert report["evidence_source"] == "live_openobserve"
    assert report["input"]["trace_ids"] == ["a" * 32]
    assert report["diagnosis"] == "Observed result"
    assert report["reference_diagnosis"] == case["root_cause"]
    assert report["scoring"] == "not_scored"


def test_live_report_records_the_seeded_dataset_and_run_specific_trace_ids():
    corpus = cases()
    results = {
        case["id"]: {
            "evaluation_run_id": "run-123",
            "evaluation_trace_id": f"{index + 1:032x}",
            "seeded_record_count": 40,
            "analysis": "Observed result",
            "evidence": [],
            "evidence_status": "insufficient_data",
        }
        for index, case in enumerate(corpus)
    }

    report = build_report(corpus, mode="live", results=results)

    assert report["evaluation_run_id"] == "run-123"
    assert report["seeded_record_count"] == 40
    assert report["cases"][0]["input"]["trace_ids"] == [f"{1:032x}"]


def test_unknown_case_and_invalid_mode_fail_closed():
    case = cases()[0]
    with pytest.raises(ValueError, match="unknown evaluation case"):
        build_case_report({**case, "id": "unknown"}, mode="mock")
    with pytest.raises(ValueError, match="mode must be"):
        build_case_report(case, mode="other")
