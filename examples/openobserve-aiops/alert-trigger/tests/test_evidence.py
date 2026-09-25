from holmes_client import extract_evidence
from tasks import PermanentTaskError


def test_search_query_must_target_exactly_one_allowlisted_stream():
    good = {
        "tool_name": "openobserve_search_logs",
        "result": {
            "status": "success",
            "params": {"sql": 'SELECT * FROM "app_logs" WHERE level = \'error\''},
            "data": {"hits": [{"trace_id": "abc"}]},
        },
    }
    bad = {
        "tool_name": "openobserve_search_logs",
        "result": {
            "status": "success",
            "params": {"sql": "SELECT * FROM secrets"},
            "data": {"hits": [{"token": "hidden"}]},
        },
    }
    evidence, verified = extract_evidence([good, bad])
    assert verified is True
    assert len(evidence) == 1
    assert evidence[0]["params"]["sql"].startswith("SELECT")


def test_invalid_tool_result_shape_is_not_trusted():
    try:
        extract_evidence({"unexpected": "object"})
    except PermanentTaskError as exc:
        assert exc.code == "holmes_tool_calls_invalid"
    else:
        raise AssertionError("invalid tool result was trusted")
