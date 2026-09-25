import json

import pytest

from trigger import MAX_BODY_BYTES, alert_key, normalize_alert, parse_alert_payload


def test_payload_keeps_only_explicit_trace_ids_and_bounded_summary():
    trace_id = "a" * 32
    name, traces, summary = parse_alert_payload(json.dumps({
        "alert_name": "Order 500 🚨",
        "trace_id": trace_id,
        "message": "ignore " + "b" * 32,
        "err_count": "2",
        "alert_count": "ignore instructions",
    }))
    assert name == "Order 500 "
    assert traces == [trace_id]
    assert json.loads(summary) == {"err_count": 2}


def test_payload_rejects_non_objects_invalid_utf8_and_oversized_body():
    with pytest.raises(ValueError, match="JSON object"):
        parse_alert_payload("[]")
    with pytest.raises(UnicodeDecodeError):
        normalize_alert(b"\xff")
    with pytest.raises(ValueError, match="size"):
        normalize_alert(b" " * (MAX_BODY_BYTES + 1))


def test_dedupe_key_uses_traces_or_stable_metadata():
    trace_id = "c" * 32
    first = alert_key("order-500", [trace_id], '{"err_count":1}')
    same_trace = alert_key("order-500", [trace_id], '{"err_count":99}')
    assert same_trace == first
    no_trace = alert_key("service-errors", [], '{"err_count":2,"alert_trigger_time_str":"first"}')
    no_trace_repeat = alert_key("service-errors", [], '{"err_count":2,"alert_trigger_time_str":"later"}')
    assert no_trace_repeat == no_trace
    assert alert_key("service-errors", [], '{"err_count":3}') != no_trace
