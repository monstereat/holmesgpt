"""Strict stream isolation and secret-safe upstream errors."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.openobserve.openobserve import (
    OpenObserveConfig, OpenObserveFindTrace, OpenObserveListStreams,
    OpenObserveSearchLogs, OpenObserveToolset,
)


@pytest.fixture
def toolset():
    s = OpenObserveToolset()
    s.config = OpenObserveConfig(
        api_url="https://observe.example.test", organization="prod",
        username="svc@example.test", password="demo-secret",
        allowed_streams=["frontend_logs", "backend_logs"], max_rows=3,
    )
    return s


def test_allowed_stream_query(toolset, monkeypatch):
    seen = []
    monkeypatch.setattr(toolset, "_request", lambda *a, **kw: (
        seen.append(kw["json_body"]), {"hits": [], "total": 0}
    )[1])
    result = OpenObserveSearchLogs(toolset)._invoke({
        "sql": 'SELECT * FROM "frontend_logs" WHERE level = \'error\'',
        "start_time": 1_700_000_000_000_000,
        "end_time": 1_700_000_060_000_000,
    }, None)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert len(seen) == 1


@pytest.mark.parametrize("sql", [
    'SELECT * FROM "secret_logs"',
    'SELECT * FROM "frontend_logs", "secret_logs"',
    'SELECT * FROM "frontend_logs" JOIN "secret_logs" ON 1=1',
    'SELECT * FROM (SELECT * FROM "secret_logs") x',
    'WITH x AS (SELECT * FROM "secret_logs") SELECT * FROM x',
    'SELECT * FROM "frontend_logs" UNION SELECT * FROM "secret_logs"',
    'SELECT * FROM "frontend_logs" -- ignore permission',
])
def test_strict_scope_denies_unauthorized_and_complex_sql(toolset, monkeypatch, sql):
    monkeypatch.setattr(toolset, "_request", lambda *a, **kw: pytest.fail("network called"))
    result = OpenObserveSearchLogs(toolset)._invoke({
        "sql": sql, "start_time": 1000000, "end_time": 2000000,
    }, None)
    assert result.status == StructuredToolResultStatus.ERROR


def test_trace_tool_respects_stream_scope(toolset, monkeypatch):
    monkeypatch.setattr(toolset, "_request", lambda *a, **kw: pytest.fail("network called"))
    result = OpenObserveFindTrace(toolset)._invoke({
        "stream": "secret_logs", "trace_id": "a" * 32,
        "start_time": 1000000, "end_time": 2000000,
    }, None)
    assert result.status == StructuredToolResultStatus.ERROR


def test_stream_listing_filtered(toolset, monkeypatch):
    monkeypatch.setattr(toolset, "_request", lambda *a, **kw: {
        "list": [{"name": "frontend_logs", "schema": []}, {"name": "secret_logs", "schema": []}]
    })
    result = OpenObserveListStreams(toolset)._invoke({}, None)
    assert [s["name"] for s in result.data["streams"]] == ["frontend_logs"]


def test_http_error_redacts_sensitive_upstream_message(toolset, monkeypatch):
    response = Mock(status_code=403)
    response.text = "authorization denied: demo-secret"
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    monkeypatch.setattr(requests, "request", lambda *a, **kw: response)
    result = OpenObserveListStreams(toolset)._invoke({}, None)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "demo-secret" not in result.error
    assert "403" in result.error
