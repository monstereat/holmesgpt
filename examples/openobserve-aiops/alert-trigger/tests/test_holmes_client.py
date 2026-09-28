import json
import socket
from email.message import Message
from io import BytesIO

import pytest
from urllib.error import HTTPError, URLError

from holmes_client import HolmesClient, build_investigation_question, extract_evidence
from evaluation import has_evaluation_record
from task_errors import PermanentTaskError, RetryableTaskError

SCOPED_TEST_TASK = {"alert_name": "order-500", "trace_ids": ["a" * 32], "summary": {}}


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
    assert "Do not cite rows from any other run" in eval_question
    assert "Every openobserve_search_logs query must include both exact" in eval_question
    assert "This also applies to release_deployed checks" in eval_question
    assert "If a query is rejected, correct its SQL before retrying" in eval_question
    assert "Provide read-only next steps only" in eval_question
    assert "approval and change process are required first" in eval_question
    assert "trusted server-generated alert search window" not in eval_question


def test_evaluation_scope_is_forwarded_as_server_context_headers():
    trace_id = "a" * 32
    opener = FakeOpener(FakeResponse({"analysis": "Found evidence", "tool_calls": [_tool()]}))
    client = HolmesClient("http://holmes:5050", "service-key", opener=opener)
    task = {
        "alert_name": "alert",
        "trace_ids": [trace_id],
        "summary": {
            "evaluation_run_id": "b" * 32,
            "evaluation_case_id": "case-1",
            "evaluation_timestamp_us": 1_800_000_000_000_000,
        },
    }

    client.investigate(task, evaluation=True)

    assert opener.request.get_header("X-aiops-evaluation-run-id") == "b" * 32
    assert opener.request.get_header("X-aiops-evaluation-case-id") == "case-1"
    assert opener.request.get_header("X-aiops-trace-ids") == trace_id


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


def test_investigation_fails_closed_without_trace_or_alert_timestamp():
    client = HolmesClient("http://holmes:5050", "service-key", opener=FakeOpener())
    with pytest.raises(PermanentTaskError, match="task_scope_missing"):
        client.investigate({"alert_name": "alert", "trace_ids": [], "summary": {}})


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
    mixed_evidence, verified = extract_evidence([_tool(), _tool("error")])
    assert verified is True
    assert [item["status"] for item in mixed_evidence] == ["success", "error"]
    assert mixed_evidence[1]["error_code"] == "openobserve_tool_error"
    assert mixed_evidence[1]["data"] is None
    with pytest.raises(PermanentTaskError, match="no_openobserve_calls"):
        extract_evidence([unsafe_tool])
    with pytest.raises(PermanentTaskError, match="holmes_tool_error") as error:
        extract_evidence([_tool("error")])
    assert error.value.evidence[0]["status"] == "error"
    assert error.value.evidence[0]["error_code"] == "openobserve_tool_error"


def test_find_trace_uses_the_generated_sql_and_must_match_the_alert_trace_id():
    trace_id = "a" * 32
    call = {
        "tool_name": "openobserve_find_trace",
        "result": {
            "status": "success",
            "params": {
                "sql": f'SELECT * FROM "app_logs" WHERE trace_id = \'{trace_id}\' ORDER BY _timestamp DESC',
                "start_time": 100,
                "end_time": 200,
            },
            "data": {"hits": [{"trace_id": trace_id, "evaluation_run_id": "run-1", "evaluation_case_id": "case-1"}]},
        },
    }

    evidence, verified = extract_evidence([call], expected_trace_ids=[trace_id])

    assert verified is True
    assert evidence[0]["tool_name"] == "openobserve_find_trace"
    assert has_evaluation_record(evidence, run_id="run-1", case_id="case-1")
    with pytest.raises(PermanentTaskError, match="holmes_no_openobserve_calls"):
        extract_evidence([call], expected_trace_ids=["b" * 32])
    with pytest.raises(PermanentTaskError, match="holmes_no_openobserve_calls"):
        extract_evidence([call], expected_trace_ids=[])


def test_search_evidence_rejects_out_of_scope_query_or_rows():
    trace_id = "a" * 32
    window = {"search_window_start_unix_us": 100, "search_window_end_unix_us": 200}
    call = {
        "tool_name": "openobserve_search_logs",
        "result": {
            "status": "success",
            "params": {
                "sql": f"SELECT * FROM app_logs WHERE trace_id IN ('{trace_id}')",
                "start_time": 100,
                "end_time": 200,
            },
            "data": {"hits": [{"trace_id": trace_id, "_timestamp": 150}], "query": {"start_time": 100, "end_time": 200}},
        },
    }
    evidence, verified = extract_evidence(
        [call], expected_trace_ids=[trace_id], expected_search_window=window, enforce_task_scope=True
    )
    assert verified is True
    assert len(evidence) == 1

    for bad_hit in ({"trace_id": "b" * 32, "_timestamp": 150}, {"trace_id": trace_id, "_timestamp": 201}):
        scoped = {**call, "result": {**call["result"], "data": {"hits": [bad_hit], "query": {"start_time": 100, "end_time": 200}}}}
        with pytest.raises(PermanentTaskError, match="holmes_no_openobserve_calls"):
            extract_evidence(
                [scoped], expected_trace_ids=[trace_id], expected_search_window=window, enforce_task_scope=True
            )


def test_openobserve_toolset_readiness_uses_authenticated_info_endpoint():
    response = FakeResponse({
        "toolsets": [{"name": "openobserve", "enabled": True, "status": "enabled", "tool_count": 3}],
    })
    opener = FakeOpener(response)
    client = HolmesClient("https://holmes.internal", "service-key", opener=opener)

    client.check_openobserve_toolset()

    assert opener.request.full_url == "https://holmes.internal/api/info?detail=full"
    assert opener.request.get_header("X-api-key") == "service-key"
    assert opener.timeout == 10


@pytest.mark.parametrize(
    "toolset, expected_code",
    [
        ({"name": "openobserve", "enabled": False, "status": "disabled"}, "holmes_toolset_unavailable"),
        ({"name": "openobserve", "enabled": True, "status": "failed"}, "holmes_toolset_unavailable"),
    ],
)
def test_openobserve_toolset_unavailability_is_retryable(toolset, expected_code):
    client = HolmesClient(
        "http://holmes",
        "service-key",
        opener=FakeOpener(FakeResponse({"toolsets": [toolset]})),
    )

    with pytest.raises(RetryableTaskError) as raised:
        client.check_openobserve_toolset()

    assert raised.value.code == expected_code


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
        client.investigate(SCOPED_TEST_TASK)
    assert raised.value.code == expected_code
    assert "secret" not in str(raised.value)


def test_rate_limit_is_retryable():
    status = 429
    error = HTTPError("http://holmes/api/chat", status, "secret upstream details", Message(), BytesIO(b"secret"))
    client = HolmesClient("http://holmes", "key", opener=FakeOpener(error=error))
    with pytest.raises(RetryableTaskError) as raised:
        client.investigate(SCOPED_TEST_TASK)
    assert raised.value.code == "holmes_rate_limited"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("status", [500, 503])
def test_server_errors_have_unknown_outcome_and_are_not_retried(status):
    error = HTTPError("http://holmes/api/chat", status, "secret upstream details", Message(), BytesIO(b"secret"))
    client = HolmesClient("http://holmes", "key", opener=FakeOpener(error=error))
    with pytest.raises(PermanentTaskError) as raised:
        client.investigate(SCOPED_TEST_TASK)
    assert raised.value.code == "holmes_outcome_unknown"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("error", [TimeoutError("private timeout detail"), URLError("private network detail"), socket.timeout("private socket detail")])
def test_transport_failures_have_unknown_outcome_and_are_not_retried(error):
    client = HolmesClient("http://holmes", "key", opener=FakeOpener(error=error))
    with pytest.raises(PermanentTaskError) as raised:
        client.investigate(SCOPED_TEST_TASK)
    assert raised.value.code == "holmes_outcome_unknown"
    assert "private" not in str(raised.value)


def test_invalid_response_and_redirects_fail_closed():
    malformed = HolmesClient("http://holmes", "key", opener=FakeOpener(FakeResponse({"analysis": "answer", "tool_calls": []})))
    with pytest.raises(PermanentTaskError, match="holmes_no_openobserve_calls"):
        malformed.investigate(SCOPED_TEST_TASK)
    with pytest.raises(ValueError, match="embedded credentials"):
        HolmesClient("http://user:password@holmes", "key")
