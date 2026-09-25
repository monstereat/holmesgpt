import json
import re
from pathlib import Path

import pytest
import yaml

import seed_local_evidence as seed_evidence_module
from seed_local_evidence import _validate_local_url, build_records, seed_local_evidence
from run_evals import evaluation_alert_timestamp_us, load_evaluation_contexts, validate_local_holmes_url
from holmes_client import build_investigation_question


CASE = {
    "id": "order-eval-case",
    "alert": "OrderCreateFailure",
    "service": "order-service",
    "evidence": ["Trace 0123456789abcdef0123456789abcdef: inventory.reserve returned 409.", "Order persistence was not called."],
}


def test_build_records_scopes_case_evidence_to_unique_synthetic_trace():
    records, trace_ids, timestamps_us, counts = build_records([CASE], "a" * 32, 1_800_000_000_000_000)

    trace_id = trace_ids[CASE["id"]]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert len(records) == 2
    assert all(record["trace_id"] == trace_id for record in records)
    assert all(record["evaluation_run_id"] == "a" * 32 for record in records)
    assert all(record["evaluation_case_id"] == CASE["id"] for record in records)
    assert all(record["evaluation_source"] == "synthetic_fixture" for record in records)
    assert trace_id in records[0]["message"]
    assert timestamps_us == {CASE["id"]: 1_800_000_000_000_000}
    assert counts == {CASE["id"]: 2}


def test_release_case_seeds_an_explicit_synthetic_release_event():
    corpus_path = Path(__file__).with_name("known_root_causes.json")
    cases = json.loads(corpus_path.read_text(encoding="utf-8"))
    case = next(item for item in cases if item["id"] == "order-release-regression")
    context = load_evaluation_contexts()[case["id"]]

    records, trace_ids, timestamps_us, counts = build_records([case], "b" * 32, 1_800_000_000_000_000, {case["id"]: context})
    release = next(record for record in records if record["event_type"] == "release_deployed")

    assert release["evaluation_source"] == "synthetic_fixture"
    assert release["evaluation_run_id"] == "b" * 32
    assert release["evaluation_case_id"] == case["id"]
    assert release["release"] == "v2.4.1"
    assert release["commit_sha"] is None
    assert release["changed_files"] == ["src/orders/mapper.ts"]
    assert "trace_id" not in release
    assert all(
        record["message"] != context["release_evidence"]
        for record in records
        if record["event_type"] != "release_deployed"
    )
    assert any(trace_ids[case["id"]] in record["message"] for record in records)
    assert timestamps_us[case["id"]] == 1_800_000_000_000_000
    assert counts[case["id"]] == len(records)


def test_case_timestamps_are_farther_apart_than_proxy_query_window():
    cases = [dict(CASE, id=f"case-{index}") for index in range(3)]
    _, _, timestamps_us, _ = build_records(cases, "c" * 32, 1_800_000_000_000_000)

    ordered = list(timestamps_us.values())
    assert all(later - earlier == seed_evidence_module.CASE_TIMESTAMP_INTERVAL_US for earlier, later in zip(ordered, ordered[1:]))
    assert seed_evidence_module.CASE_TIMESTAMP_INTERVAL_US > 60 * 60 * 1_000_000


def test_live_context_loader_supplies_repository_runbook_and_release_metadata():
    context = load_evaluation_contexts()["order-database-deploy-mismatch"]

    assert context["release"] == "v1.2.0"
    assert context["commit_sha"] is None
    assert context["runbook_context"]["source"] == "repository_runbook"
    assert context["runbook_context"]["path"].endswith("order-service-database-schema-mismatch.md")
    assert "migration history" in context["runbook_context"]["content"]

    question = build_investigation_question({
        "alert_name": CASE["alert"],
        "summary": {
            "runbook_context": context["runbook_context"],
            "release_context": context,
            "evaluation_run_id": "b" * 32,
            "evaluation_case_id": "order-database-deploy-mismatch",
            "evaluation_timestamp_us": 1_800_000_000_000_000,
        },
    }, evaluation=True)
    assert context["runbook_context"]["path"] in question
    assert "release_deployed" in question
    assert "do not infer a commit" in question
    assert "exact evaluation_run_id AND evaluation_case_id" in question


def test_compose_uses_holmes_and_litellm_environment_names_for_deepseek():
    compose_path = Path(__file__).parents[1] / "docker-compose.yaml"
    config_path = Path(__file__).parents[1] / "holmes-config" / "config.yaml.example"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    environment = compose["services"]["holmes-api"]["environment"]
    assert compose["services"]["openobserve"]["environment"]["ZO_INGEST_ALLOWED_UPTO"] == "24"
    assert environment["MODEL"] == "${HOLMES_MODEL:-deepseek/deepseek-flash}"
    assert environment["DEEPSEEK_API_KEY"] == "${DEEPSEEK_API_KEY:-}"
    assert "HOLMES_MODEL" not in environment
    assert "MODEL_API_KEY" not in environment
    assert config["max_steps"] == 12
    assert "model" not in config
    assert "api_key" not in config


@pytest.mark.parametrize("url", [
    "https://api.openobserve.ai",
    "http://192.168.1.9:5080",
    "http://127.0.0.1:8081",
    "http://user:pass@127.0.0.1:5080",
])
def test_seed_url_rejects_nonlocal_or_unexpected_destinations(url):
    with pytest.raises(ValueError, match="local OpenObserve"):
        _validate_local_url(url)


@pytest.mark.parametrize("url", [
    "https://api.holmes.example",
    "http://192.168.1.9:5050",
    "http://127.0.0.1:8081",
    "http://user:pass@127.0.0.1:5050",
])
def test_holmes_evaluation_target_must_be_the_local_api(url):
    with pytest.raises(ValueError, match="local Holmes"):
        validate_local_holmes_url(url)


def test_holmes_evaluation_accepts_local_api():
    assert validate_local_holmes_url("http://localhost:5050/") == "http://localhost:5050"


def test_seed_local_evidence_uses_local_json_ingest_and_checks_accepted_count():
    class Response:
        def __init__(self, result):
            self.status = 200
            self.result = result

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return json.dumps(self.result).encode()

    class Opener:
        requests = []

        def open(self, request, timeout):
            self.requests.append(request)
            if request.full_url.endswith("/app_logs/_json"):
                assert timeout == 15
                return Response({"status": [{"successful": 2, "failed": 0}]})
            assert request.full_url == "http://127.0.0.1:5080/api/default/_search"
            assert timeout == 10
            return Response({"total": 2, "hits": []})

    opener = Opener()
    dataset = seed_local_evidence(
        [CASE], base_url="http://127.0.0.1:5080", username="local-test", password="not-a-real-secret", opener=opener
    )

    ingest_request, search_request = opener.requests
    assert ingest_request.full_url == "http://127.0.0.1:5080/api/default/app_logs/_json"
    records = json.loads(ingest_request.data)
    assert len(records) == dataset["record_count"] == 2
    assert dataset["trace_ids"][CASE["id"]] == records[0]["trace_id"]
    assert dataset["run_id"] == records[0]["evaluation_run_id"]
    assert dataset["case_timestamps_us"][CASE["id"]] == records[0]["_timestamp"]
    search = json.loads(search_request.data)
    assert search_request.full_url == "http://127.0.0.1:5080/api/default/_search"
    assert dataset["run_id"] in search["query"]["sql"]
    assert CASE["id"] in search["query"]["sql"]
    assert search["query"]["size"] == 2


def test_seed_local_evidence_rejects_partial_ingestion_response():
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return b'{"status":[{"successful":1,"failed":1}]}'

    class Opener:
        def open(self, _request, timeout):
            return Response()

    with pytest.raises(ValueError, match="did not accept every"):
        seed_local_evidence(
            [CASE], base_url="http://localhost:5080", username="local-test", password="not-a-real-secret", opener=Opener()
        )


def test_search_visibility_waits_for_all_current_run_records(monkeypatch):
    class Response:
        def __init__(self, total):
            self.total = total

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return json.dumps({"total": self.total}).encode()

    class Opener:
        totals = iter((0, 2))
        calls = 0

        def open(self, _request, timeout):
            self.calls += 1
            assert timeout == 10
            return Response(next(self.totals))

    monkeypatch.setattr(seed_evidence_module, "SEARCH_VISIBILITY_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(seed_evidence_module, "SEARCH_VISIBILITY_POLL_SECONDS", 0)
    opener = Opener()
    seed_evidence_module._wait_for_search_visibility(
        "http://127.0.0.1:5080",
        "local-test",
        "not-a-real-secret",
        "c" * 32,
        {CASE["id"]: 1_800_000_000_000_000},
        {CASE["id"]: 2},
        opener,
    )
    assert opener.calls == 2


def test_evaluation_alert_occurs_after_seeded_evidence_for_exclusive_search_bounds():
    assert evaluation_alert_timestamp_us(1_800_000_000_000_000) == 1_800_000_001_000_000
