import json
import socket
from email.message import Message
from io import BytesIO

import pytest
from urllib.error import HTTPError, URLError

from holmes_client import HolmesClient, build_investigation_question, extract_evidence
from evaluation import has_evaluation_record
from task_errors import PermanentTaskError, RetryableTaskError


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.content = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.content


class FakeOpener:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.result


def _tool(status="success", *, stream="app_logs", data=None):
    return {
        "tool_name": "openobserve_find_trace",
        "result": {
            "status": status,
            "params": {"stream": stream, "trace_id": "a" * 32},
            "data": data if data is not None else {"hits": [{"message": "inventory query failed"}]},
        },
    }


def test_request_matches_holmes_non_streaming_api_contract():
    opener = FakeOpener(FakeResponse({"analysis": "Root cause is inventory timeout", "tool_calls": [_tool()]}))
    client = HolmesClient("http://holmes:5050/", "service-key", timeout_seconds=90, opener=opener)
    result = client.investigate({"task_id": "task-1", "alert_name": "order-500", "trace_ids": ["a" * 32], "summary": {}})
    request = opener.request
    assert request.full_url == "http://holmes:5050/api/chat"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == "service-key"
    assert request.get_header("Content-type") == "application/json"
    assert opener.timeout == 90
    payload = json.loads(request.data)
    assert payload["stream"] is False
    assert set(payload) == {"ask", "stream"}
    assert "untrusted data" in payload["ask"]
    assert "task-1" not in payload["ask"]
    assert result["evidence_status"] == "verified"
    assert result["evidence"][0]["tool_name"] == "openobserve_find_trace"


def test_protocol_markup_cannot_be_persisted_as_a_completed_diagnosis():
    response = FakeResponse({
        "analysis": "<|tool_call_begin|>openobserve_search_logs<|tool_call_end|>",
        "tool_calls": [_tool()],
    })
    client = HolmesClient("http://holmes:5050", "service-key", opener=FakeOpener(response))

    with pytest.raises(PermanentTaskError, match="holmes_analysis_not_readable"):
        client.investigate({"alert_name": "order-500", "trace_ids": ["a" * 32], "summary": {}})


def test_synthetic_evaluation_instructions_require_internal_mode_flag():
    task = {
        "alert_name": "alert",
        "trace_ids": ["a" * 32],
        "summary": {
            "fixture_source": "synthetic_known_root_cause_case",
            "evaluation_case_id": "case-1",
            "evaluation_run_id": "b" * 32,
            "evaluation_timestamp_us": 1_800_000_000_000_000,
            "alert_trigger_time_str": "2026-09-25T14:23:41Z",
        },
    }

    normal_question = build_investigation_question(task)
    eval_question = build_investigation_question(task, evaluation=True)

    assert "Do not call shell/bash tools" in normal_question
    assert "make no more than three OpenObserve calls" in normal_question
    assert "Stop broadening the search after exact trace evidence is found" in normal_question
    assert "Treat these records as test fixtures" not in normal_question
    assert "Treat these records as test fixtures" in eval_question
    assert '"evaluation_run_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' in eval_question
    assert '"evaluation_case_id":"case-1"' in eval_question
    assert '"search_window_start_unix_us":1799999940000000' in eval_question
    assert "do not cite rows from any other run" in eval_question
    assert "trusted server-generated alert search window" not in eval_question


def test_production_alert_search_window_is_server_anchored_and_proxy_bounded():
    question = build_investigation_question({
        "alert_name": "order-500",
        "trace_ids": ["a" * 32],
        "summary": {"alert_trigger_time_str": "2026-09-25T14:23:41Z"},
    })

    assert '"alert_time_unix_us":1790346221000000' in question
    assert '"search_window_start_unix_us":1790345921000000' in question
    assert '"search_window_end_unix_us":1790346281000000' in question
    assert "Use these exact start_time and end_time values" in question
    assert "do not center a new window or extend it" in question


def test_invalid_alert_timestamp_does_not_create_search_anchors():
    question = build_investigation_question({
        "alert_name": "order-500",
        "trace_ids": [],
        "summary": {"alert_trigger_time_str": "not-a-date"},
    })

    assert "No valid alert timestamp was supplied" in question
    assert "alert_time_unix_us" not in question


def test_synthetic_evaluation_requires_run_case_and_timestamp_anchors():
    with pytest.raises(ValueError, match="requires valid run, case, and alert-time anchors"):
        build_investigation_question(
            {"alert_name": "alert", "trace_ids": ["a" * 32], "summary": {"evaluation_case_id": "case-1"}},
            evaluation=True,
        )


def test_only_successful_allowlisted_openobserve_calls_become_verified_evidence():
    safe = _tool(data={"message": "token=secret-value Authorization: Bearer abc.def.ghi"})
    unsafe_stream = _tool(stream="customer_private")
    unsafe_tool = {"tool_name": "bash", "result": {"status": "success", "data": "changed"}}
    evidence, verified = extract_evidence([safe, unsafe_stream, unsafe_tool])
    assert verified is True
    assert len(evidence) == 1
    assert evidence[0]["data"]["message"] == "token=[redacted] Authorization: Bearer [redacted]"

    empty_evidence, verified = extract_evidence([_tool("no_data", data=None)])
    assert verified is False
    assert empty_evidence[0]["status"] == "no_data"
    with pytest.raises(PermanentTaskError, match="no_openobserve_calls"):
        extract_evidence([unsafe_tool])
    with pytest.raises(PermanentTaskError, match="holmes_tool_error"):
        extract_evidence([_tool("error")])


def test_json_encoded_openobserve_results_remain_structured_for_fixture_matching():
    data = {
        "hits": [{
            "evaluation_run_id": "run-123",
            "evaluation_case_id": "case-123",
            "event_type": "release_deployed",
        }]
    }
    evidence, verified = extract_evidence([_tool(data=json.dumps(data))])

    assert verified is True
    assert isinstance(evidence[0]["data"], dict)
    assert has_evaluation_record(
        evidence,
        run_id="run-123",
        case_id="case-123",
        event_type="release_deployed",
    )


@pytest.mark.parametrize("status, expected_code", [(401, "holmes_auth_failed"), (403, "holmes_auth_failed"), (400, "holmes_request_rejected")])
def test_permanent_http_errors_are_classified_without_returning_upstream_body(status, expected_code):
    headers = Message()
    error = HTTPError("http://holmes/api/chat", status, "upstream-key-must-not-leak", headers, BytesIO(b"api-key=secret"))
    client = HolmesClient("http://holmes", "do-not-log", opener=FakeOpener(error=error))
    with pytest.raises(PermanentTaskError) as raised:
        client.investigate({})
    assert raised.value.code == expected_code
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("status, expected_code", [(429, "holmes_rate_limited"), (500, "holmes_unavailable"), (503, "holmes_unavailable")])
def test_rate_limit_and_server_errors_are_retryable(status, expected_code):
    error = HTTPError("http://holmes/api/chat", status, "secret upstream details", Message(), BytesIO(b"secret"))
    client = HolmesClient("http://holmes", "key", opener=FakeOpener(error=error))
    with pytest.raises(RetryableTaskError) as raised:
        client.investigate({})
    assert raised.value.code == expected_code
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("error", [TimeoutError("private timeout detail"), URLError("private network detail"), socket.timeout("private socket detail")])
def test_transport_failures_are_retryable_and_redacted(error):
    client = HolmesClient("http://holmes", "key", opener=FakeOpener(error=error))
    with pytest.raises(RetryableTaskError) as raised:
        client.investigate({})
    assert raised.value.code == "holmes_timeout"
    assert "private" not in str(raised.value)


def test_invalid_response_and_redirects_fail_closed():
    malformed = HolmesClient("http://holmes", "key", opener=FakeOpener(FakeResponse({"analysis": "answer", "tool_calls": []})))
    with pytest.raises(PermanentTaskError, match="holmes_no_openobserve_calls"):
        malformed.investigate({})
    with pytest.raises(ValueError, match="embedded credentials"):
        HolmesClient("http://user:password@holmes", "key")
