from types import SimpleNamespace

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.openobserve.openobserve import (
    OpenObserveConfig,
    OpenObserveSearchLogs,
    OpenObserveToolset,
)


def make_toolset(max_rows: int = 25) -> OpenObserveToolset:
    toolset = OpenObserveToolset()
    toolset.config = OpenObserveConfig(
        api_url="https://observe.example.com",
        organization="default",
        username="svc@example.com",
        password="secret",
        max_rows=max_rows,
    )
    return toolset


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM logs",
        "UPDATE logs SET level = 'info'",
        "SELECT * FROM logs; DROP TABLE logs",
        "PRAGMA table_info(logs)",
    ],
)
def test_search_rejects_mutating_or_multi_statement_sql(sql):
    with pytest.raises(ValueError):
        OpenObserveSearchLogs.validate_sql(sql)


def test_search_accepts_read_only_select_and_cte():
    assert OpenObserveSearchLogs.validate_sql("SELECT * FROM logs") == "SELECT * FROM logs"
    assert OpenObserveSearchLogs.validate_sql(
        "WITH recent AS (SELECT * FROM logs) SELECT * FROM recent"
    ).startswith("WITH recent")


def test_search_caps_rows_and_forwards_explicit_time_range(monkeypatch):
    toolset = make_toolset(max_rows=10)
    observed = {}

    def fake_request(method, path, *, json_body=None, params=None, timeout=None):
        observed.update(
            {
                "method": method,
                "path": path,
                "json_body": json_body,
                "params": params,
                "timeout": timeout,
            }
        )
        return {
            "hits": [{"message": f"row-{i}"} for i in range(20)],
            "total": 20,
            "took": 12,
            "scan_size": 100,
        }

    monkeypatch.setattr(toolset, "_request", fake_request)
    search = OpenObserveSearchLogs(toolset)
    result = search._invoke(
        {
            "sql": "SELECT message FROM app_logs",
            "start_time": 1_700_000_000_000_000,
            "end_time": 1_700_000_060_000_000,
            "size": 500,
        },
        SimpleNamespace(request_context={}),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data["size"] == 10
    assert len(result.data["hits"]) == 10
    query = observed["json_body"]["query"]
    assert query["size"] == 10
    assert query["start_time"] == 1_700_000_000_000_000
    assert query["end_time"] == 1_700_000_060_000_000


def test_search_rejects_invalid_time_range(monkeypatch):
    toolset = make_toolset()
    called = False

    def fake_request(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(toolset, "_request", fake_request)
    search = OpenObserveSearchLogs(toolset)
    result = search._invoke(
        {
            "sql": "SELECT * FROM app_logs",
            "start_time": 10,
            "end_time": 9,
        },
        SimpleNamespace(request_context={}),
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert called is False


def test_prerequisite_sets_validated_config(monkeypatch):
    toolset = OpenObserveToolset()

    def fake_request(method, path, *, json_body=None, params=None, timeout=None):
        return {"list": [{"name": "app_logs"}]}

    monkeypatch.setattr(toolset, "_request", fake_request)
    ok, message = toolset.prerequisites_callable(
        {
            "api_url": "https://observe.example.com",
            "organization": "default",
            "username": "svc@example.com",
            "password": "secret",
        }
    )

    assert ok is True
    assert "1 log streams" in message
