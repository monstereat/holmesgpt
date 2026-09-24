"""Unit tests for the scoped OpenObserve read-only toolset."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from holmes.plugins.toolsets.openobserve.openobserve import (
    OpenObserveConfig, OpenObserveToolset,
)

CONFIG = dict(
    api_url="https://observe.example", organization="company", stream="application",
    email="robot@example.com", api_key="test-secret",
)


def test_search_trace_scoped_sql_and_microsecond_timestamps():
    toolset = OpenObserveToolset()
    toolset.config = OpenObserveConfig(**CONFIG)
    response = Mock()
    response.json.return_value = {"hits": [{"trace_id": "abc-123", "message": "error"}]}
    with patch("holmes.plugins.toolsets.openobserve.openobserve.requests.post", return_value=response) as post:
        hits = toolset.search_trace("abc-123", minutes=5, limit=2,
                                    now=datetime(2026, 9, 24, tzinfo=timezone.utc))
    assert hits[0]["trace_id"] == "abc-123"
    args = post.call_args
    assert args.kwargs["auth"] == ("robot@example.com", "test-secret")
    assert args.kwargs["allow_redirects"] is False
    assert args.kwargs["json"]["query"]["sql"] == (
        'SELECT * FROM "application" WHERE trace_id = \'abc-123\' ORDER BY _timestamp DESC'
    )
    assert args.kwargs["json"]["query"]["end_time"] - args.kwargs["json"]["query"]["start_time"] == 300_000_000


@pytest.mark.parametrize("trace_id", ["x' OR 1=1 --", "", "x;DELETE", "x" * 129])
def test_rejects_untrusted_trace_id_without_network(trace_id):
    toolset = OpenObserveToolset()
    toolset.config = OpenObserveConfig(**CONFIG)
    with patch("holmes.plugins.toolsets.openobserve.openobserve.requests.post") as post:
        with pytest.raises(ValueError):
            toolset.search_trace(trace_id)
        post.assert_not_called()


def test_rejects_oversized_windows_and_results():
    toolset = OpenObserveToolset()
    toolset.config = OpenObserveConfig(**CONFIG)
    with pytest.raises(ValueError):
        toolset.search_trace("abc", minutes=1441)
    with pytest.raises(ValueError):
        toolset.search_trace("abc", limit=1000)


def test_toolset_registered():
    from holmes.plugins.toolsets import load_python_toolsets
    from holmes.plugins.toolsets.openobserve.openobserve import OpenObserveToolset
    # Verify registration without triggering network prerequisite checks.
    import inspect
    assert "OpenObserveToolset" in inspect.getsource(load_python_toolsets)
