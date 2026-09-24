"""Trace-aware diagnostics and request-hardening tests; no live API credentials."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from pydantic import ValidationError

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.openobserve.openobserve import (
    OpenObserveConfig,
    OpenObserveFindTrace,
    OpenObserveToolset,
)


@pytest.fixture
def toolset():
    result = OpenObserveToolset()
    result.config = OpenObserveConfig(
        api_url="https://observe.example.test",
        organization="tenant-a",
        username="svc@example.test",
        password="dummy",
        max_rows=5,
        max_window_seconds=60,
    )
    return result


def test_find_trace_builds_bounded_query(toolset, monkeypatch):
    seen = {}

    def fake_request(method, path, **kwargs):
        seen.update({"method": method, "path": path, "payload": kwargs["json_body"]})
        return {"hits": [{"trace_id": "a" * 32}], "total": 1}

    monkeypatch.setattr(toolset, "_request", fake_request)
    result = OpenObserveFindTrace(toolset)._invoke(
        {
            "stream": "frontend_logs",
            "trace_id": "A" * 32,
            "start_time": 1_700_000_000_000_000,
            "end_time": 1_700_000_030_000_000,
        },
        SimpleNamespace(request_context={}),
    )
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/tenant-a/_search"
    assert seen["payload"]["query"]["size"] == 5
    assert "trace_id = '" + "a" * 32 + "'" in seen["payload"]["query"]["sql"]


@pytest.mark.parametrize(
    "stream,trace_id",
    [
        ("logs; DROP TABLE logs", "a" * 32),
        ("../admin", "a" * 32),
        ("logs", "not-a-trace"),
        ("logs", "a" * 32 + "'"),
    ],
)
def test_invalid_trace_input_never_reaches_network(toolset, monkeypatch, stream, trace_id):
    def fail(*args, **kwargs):
        pytest.fail("Invalid trace lookup attempted a network request")

    monkeypatch.setattr(toolset, "_request", fail)
    result = OpenObserveFindTrace(toolset)._invoke(
        {
            "stream": stream,
            "trace_id": trace_id,
            "start_time": 1_700_000_000_000_000,
            "end_time": 1_700_030_000_000_000,
        },
        None,
    )
    assert result.status == StructuredToolResultStatus.ERROR


def test_query_rejects_excessive_time_window(toolset, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Oversized query attempted a network request")

    monkeypatch.setattr(toolset, "_request", fail)
    result = OpenObserveFindTrace(toolset)._invoke(
        {
            "stream": "logs",
            "trace_id": "a" * 32,
            "start_time": 1000000,
            "end_time": 63000001,
        },
        None,
    )
    assert result.status == StructuredToolResultStatus.ERROR


def test_openobserve_does_not_follow_token_leaking_redirect(toolset, monkeypatch):
    response = Mock(status_code=302)
    response.headers = {"Location": "https://attacker.example.test/collect"}
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(kwargs)
        return response

    monkeypatch.setattr(requests, "request", fake_request)
    with pytest.raises(ValueError, match="unexpected redirect"):
        toolset._request("GET", "/api/tenant-a/streams")
    assert seen["allow_redirects"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_url": "ftp://observe.example.test"},
        {"api_url": "https://username:password@observe.example.test"},
        {"api_url": "https://observe.example.test?token=leak"},
        {"organization": "../another-tenant"},
    ],
)
def test_config_rejects_unsafe_api_urls_or_organization(toolset, kwargs):
    base = toolset.config.model_dump()
    base.update(kwargs)
    with pytest.raises(ValidationError):
        OpenObserveConfig(**base)
