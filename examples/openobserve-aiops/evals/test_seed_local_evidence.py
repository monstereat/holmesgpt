import json
import re

import pytest

from seed_local_evidence import _validate_local_url, build_records, seed_local_evidence
from run_evals import validate_local_holmes_url


CASE = {
    "id": "order-eval-case",
    "alert": "OrderCreateFailure",
    "service": "order-service",
    "evidence": ["Trace 0123456789abcdef0123456789abcdef: inventory.reserve returned 409.", "Order persistence was not called."],
}


def test_build_records_scopes_case_evidence_to_unique_synthetic_trace():
    records, trace_ids = build_records([CASE], "a" * 32, 1_800_000_000_000_000)

    trace_id = trace_ids[CASE["id"]]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert len(records) == 2
    assert all(record["trace_id"] == trace_id for record in records)
    assert all(record["evaluation_run_id"] == "a" * 32 for record in records)
    assert all(record["evaluation_case_id"] == CASE["id"] for record in records)
    assert all(record["evaluation_source"] == "synthetic_fixture" for record in records)
    assert trace_id in records[0]["message"]


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
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return json.dumps({"status": [{"successful": 2, "failed": 0}]}).encode()

    class Opener:
        request = None

        def open(self, request, timeout):
            self.request = request
            assert timeout == 15
            return Response()

    opener = Opener()
    dataset = seed_local_evidence(
        [CASE], base_url="http://127.0.0.1:5080", username="local-test", password="not-a-real-secret", opener=opener
    )

    assert opener.request.full_url == "http://127.0.0.1:5080/api/default/app_logs/_json"
    records = json.loads(opener.request.data)
    assert len(records) == dataset["record_count"] == 2
    assert dataset["trace_ids"][CASE["id"]] == records[0]["trace_id"]
    assert dataset["run_id"] == records[0]["evaluation_run_id"]


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
