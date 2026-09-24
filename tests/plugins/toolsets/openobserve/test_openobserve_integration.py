"""Mocked OpenObserve API coverage; no live credentials or LLM needed."""
from unittest.mock import Mock

import pytest
import requests

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.openobserve.openobserve import (
    OpenObserveConfig,
    OpenObserveListStreams,
    OpenObserveSearchLogs,
    OpenObserveToolset,
)


@pytest.fixture
def toolset():
    toolset = OpenObserveToolset()
    toolset.config = OpenObserveConfig(
        api_url="https://openobserve.example.test",
        organization="example-org",
        username="investigator@example.test",
        password="fake-token",
        timeout_seconds=10,
        max_rows=2,
    )
    return toolset


def test_search_enforces_row_cap_and_explicit_time_range(toolset, monkeypatch):
    observed = {}

    def fake_request(method, url, **kwargs):
        observed.update(method=method, url=url, options=kwargs)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "hits": [{"message": "first"}, {"message": "second"}, {"message": "third"}],
            "total": 3,
            "took": 7,
        }
        return response

    monkeypatch.setattr(requests, "request", fake_request)
    result = OpenObserveSearchLogs(toolset)._invoke(
        {
            "sql": "SELECT * FROM app_logs",
            "start_time": 1_700_000_000_000_000,
            "end_time": 1_700_000_060_000_000,
            "size": 5000,
        },
        None,
    )
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data["size"] == 2
    assert len(result.data["hits"]) == 2
    assert observed["method"] == "POST"
    assert observed["url"] == "https://openobserve.example.test/api/example-org/_search"
    assert observed["options"]["json"]["query"]["size"] == 2
    assert observed["options"]["json"]["query"]["start_time"] == 1_700_000_000_000_000
    assert observed["options"]["auth"].username == "investigator@example.test"
    assert observed["options"]["verify"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"sql": "SELECT * FROM app_logs", "start_time": 100, "end_time": 100},
        {"sql": "SELECT * FROM app_logs", "start_time": 0, "end_time": 200},
        {"sql": "DELETE FROM app_logs", "start_time": 100, "end_time": 200},
        {"sql": "SELECT * FROM app_logs; DROP TABLE app_logs", "start_time": 100, "end_time": 200},
    ],
)
def test_invalid_queries_never_reach_network(toolset, monkeypatch, params):
    def disallow_network(*args, **kwargs):
        pytest.fail("Invalid query attempted a network request")

    monkeypatch.setattr(requests, "request", disallow_network)
    result = OpenObserveSearchLogs(toolset)._invoke(params, None)
    assert result.status == StructuredToolResultStatus.ERROR


def test_list_streams_only_returns_expected_fields(toolset, monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "list": [
            {
                "name": "app_logs",
                "stream_type": "logs",
                "schema": [{"name": "trace_id"}],
                "stats": {"doc_num": 10},
                "secret_unexpected_field": "not forwarded",
            }
        ]
    }
    monkeypatch.setattr(requests, "request", lambda *a, **kw: response)
    result = OpenObserveListStreams(toolset)._invoke({}, None)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data["streams"][0]["name"] == "app_logs"
    assert "secret_unexpected_field" not in result.data["streams"][0]
