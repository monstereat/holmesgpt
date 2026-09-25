import pytest

from evaluation import (
    CLUSTER_BY_CASE,
    CLUSTER_TAXONOMY,
    build_case_report,
    build_report,
    cross_case_exposure_case_ids,
    has_evaluation_record,
    trace_ids,
    unscoped_successful_search_count,
)


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
    assert report["schema_version"] == "1.3.0"
    assert report["case_source"] == "synthetic_fixture"
    assert report["evaluation_run_id"] is None
    assert report["seeded_record_count"] is None
    assert report["scoring"] == "not_scored"
    assert report["counts"] == {
        "total": 20,
        "live_errors": 0,
        "matching_case_evidence": 0,
        "release_event_cases": 0,
        "matching_release_events": 0,
        "cases_with_cross_case_exposure": 0,
        "cross_case_exposure_case_count": 0,
        "cases_with_unscoped_successful_searches": 0,
        "unscoped_successful_search_count": 0,
    }
    assert report["retrieval_coverage"] == {"case_evidence_matches": 0, "release_event_matches": 0}
    assert all(case["diagnosis"] is None for case in report["cases"])
    assert all(case["assumptions"] is None for case in report["cases"])
    assert all(case["reference_expected_findings"] == ["Synthetic finding"] for case in report["cases"])
    assert all(case["reference_unsupported_claims"] == ["Synthetic unsupported claim"] for case in report["cases"])
    assert all(case["reference_safe_next_step"] == "Review with the service owner." for case in report["cases"])
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
            "evaluation_evidence_match": True,
            "evaluation_release_event_match": None,
        },
    )
    assert report["source"] == "known_root_causes.json#order-inventory-negative-stock"
    assert report["evidence_source"] == "live_openobserve"
    assert report["input"]["trace_ids"] == ["a" * 32]
    assert report["diagnosis"] == "Observed result"
    assert report["reference_diagnosis"] == case["root_cause"]
    assert report["assumptions"] is None
    assert report["reference_expected_findings"] == case["expected_findings"]
    assert report["reference_unsupported_claims"] == case["unsupported_claims"]
    assert report["reference_safe_next_step"] == case["safe_next_step"]
    assert report["scoring"] == "not_scored"
    assert report["evaluation_evidence_match"] is True
    assert report["cross_case_exposure_case_ids"] == []


def test_report_counts_exact_case_and_release_retrieval_matches():
    corpus = cases()
    results = {
        corpus[0]["id"]: {
            "evidence_status": "verified",
            "evidence": [],
            "evaluation_evidence_match": True,
            "evaluation_release_event_match": None,
        },
        corpus[1]["id"]: {
            "evidence_status": "verified",
            "evidence": [],
            "evaluation_evidence_match": False,
            "evaluation_release_event_match": True,
        },
    }

    report = build_report(corpus, mode="live", results=results)

    assert report["counts"]["matching_case_evidence"] == 1
    assert report["counts"]["release_event_cases"] == 1
    assert report["counts"]["matching_release_events"] == 1


def test_cross_case_exposure_reports_only_other_cases_from_the_current_run():
    evidence = [
        {
            "tool_name": "openobserve_search_logs",
            "status": "success",
            "data": {"hits": [
                {"evaluation_run_id": "run-1", "evaluation_case_id": "case-1"},
                {"evaluation_run_id": "run-1", "evaluation_case_id": "case-2"},
                {"evaluation_run_id": "run-old", "evaluation_case_id": "case-3"},
            ]},
        },
        {
            "tool_name": "openobserve_find_trace",
            "status": "success",
            "data": {"hits": [{"evaluation_run_id": "run-1", "evaluation_case_id": "case-4"}]},
        },
    ]

    assert cross_case_exposure_case_ids(evidence, run_id="run-1", case_id="case-1") == ["case-2", "case-4"]


def test_unscoped_successful_search_count_flags_or_and_missing_case_filters():
    evidence = [
        {
            "tool_name": "openobserve_search_logs",
            "status": "success",
            "params": {"sql": "SELECT message FROM app_logs WHERE evaluation_run_id = 'run-1' AND evaluation_case_id = 'case-1'"},
        },
        {
            "tool_name": "openobserve_search_logs",
            "status": "success",
            "params": {"sql": "SELECT message FROM app_logs WHERE evaluation_run_id = 'run-1' OR trace_id = 'abc'"},
        },
    ]

    assert unscoped_successful_search_count(evidence, run_id="run-1", case_id="case-1") == 1


def test_has_evaluation_record_requires_matching_run_and_case_and_optional_event_type():
    evidence = [{"data": {"hits": [
        {"evaluation_run_id": "run-1", "evaluation_case_id": "case-1", "event_type": "aiops_eval_evidence"},
        {"evaluation_run_id": "run-1", "evaluation_case_id": "case-1", "event_type": "release_deployed"},
    ]}}]

    assert has_evaluation_record(evidence, run_id="run-1", case_id="case-1")
    assert has_evaluation_record(evidence, run_id="run-1", case_id="case-1", event_type="release_deployed")
    assert not has_evaluation_record(evidence, run_id="run-2", case_id="case-1")
    assert not has_evaluation_record(evidence, run_id="run-1", case_id="case-2")
    assert not has_evaluation_record(evidence, run_id="run-1", case_id="case-1", event_type="other")


def test_has_evaluation_record_accepts_exact_scoped_search_when_projection_omits_ids():
    evidence = [{
        "tool_name": "openobserve_search_logs",
        "status": "success",
        "params": {
            "sql": "SELECT _timestamp, event_type, message FROM app_logs WHERE evaluation_run_id = 'run-1' AND evaluation_case_id = 'case-1'",
            "start_time": 900,
            "end_time": 1100,
        },
        "data": {"hits": [{"event_type": "aiops_eval_evidence", "message": "fixture row"}]},
    }]

    assert has_evaluation_record(evidence, run_id="run-1", case_id="case-1", seed_timestamp_us=1000)
    assert not has_evaluation_record(evidence, run_id="run-1", case_id="case-1", seed_timestamp_us=1100)
    assert not has_evaluation_record(evidence, run_id="run-2", case_id="case-1", seed_timestamp_us=1000)
    assert not has_evaluation_record(evidence, run_id="run-1", case_id="case-1", event_type="release_deployed")


def test_has_evaluation_record_scopes_release_match_to_exact_run_and_case():
    evidence = [{
        "tool_name": "openobserve_search_logs",
        "status": "success",
        "params": {
            "sql": "SELECT message FROM app_logs WHERE evaluation_run_id='run-1' AND evaluation_case_id='case-1' AND event_type='release_deployed'",
            "start_time": 900,
            "end_time": 1100,
        },
        "data": {"hits": [{"message": "release event"}]},
    }]

    assert has_evaluation_record(
        evidence, run_id="run-1", case_id="case-1", event_type="release_deployed", seed_timestamp_us=1000
    )
    evidence[0]["params"]["sql"] = evidence[0]["params"]["sql"].replace("AND event_type", "OR event_type")
    assert not has_evaluation_record(
        evidence, run_id="run-1", case_id="case-1", event_type="release_deployed", seed_timestamp_us=1000
    )


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
