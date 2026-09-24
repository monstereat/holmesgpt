"""Read-only OpenObserve trace lookup for evidence-based incident investigation."""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

import requests  # type: ignore[import-untyped]
from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite, StructuredToolResult, StructuredToolResultStatus,
    Tool, ToolInvokeContext, ToolParameter, Toolset, ToolsetTag,
)
from holmes.utils.pydantic_utils import ToolsetConfig

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_TRACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class OpenObserveConfig(ToolsetConfig):
    api_url: str = Field(description="OpenObserve URL, e.g. https://observe.example.com")
    organization: str = Field(description="OpenObserve organization ID")
    stream: str = Field(description="Name of the permitted log stream")
    email: str = Field(description="OpenObserve service-account email")
    api_key: str = Field(description="Service-account API key")
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class OpenObserveToolset(Toolset):
    config_classes: ClassVar[list[Type[OpenObserveConfig]]] = [OpenObserveConfig]

    def __init__(self):
        super().__init__(
            name="openobserve",
            enabled=False,
            description="Read-only OpenObserve trace-correlated log search",
            docs_url="https://openobserve.ai/docs/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [OpenObserveTraceLogs(self)]

    @property
    def observe_config(self) -> OpenObserveConfig:
        return self.config  # type: ignore[return-value]

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            cfg = OpenObserveConfig(**config)
            if not _IDENTIFIER.fullmatch(cfg.organization):
                return False, "Invalid OpenObserve organization ID"
            if not _IDENTIFIER.fullmatch(cfg.stream):
                return False, "Invalid OpenObserve stream name"
            if not cfg.api_url.startswith(("https://", "http://")):
                return False, "OpenObserve API URL must start with https:// or http://"
            self.config = cfg
            response = requests.get(
                cfg.api_url.rstrip("/") + "/healthz",
                timeout=cfg.timeout_seconds,
                allow_redirects=False,
            )
            response.raise_for_status()
            return True, "OpenObserve is reachable"
        except (ValueError, requests.RequestException) as exc:
            # Do not include token, URL query parameters, or HTTP response body.
            return False, f"OpenObserve prerequisite failed: {type(exc).__name__}"

    def search_trace(self, trace_id: str, minutes: int = 30, limit: int = 25,
                     now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        cfg = self.observe_config
        if not _TRACE_ID.fullmatch(trace_id):
            raise ValueError("trace_id must be 1-128 alphanumeric, '_' or '-' characters")
        if not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            raise ValueError("minutes must be between 1 and 1440")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = end - timedelta(minutes=minutes)
        sql = f'SELECT * FROM "{cfg.stream}" WHERE trace_id = \'{trace_id}\' ORDER BY _timestamp DESC'
        response = requests.post(
            f"{cfg.api_url.rstrip('/')}/api/{cfg.organization}/_search",
            auth=(cfg.email, cfg.api_key),
            json={
                "query": {
                    "sql": sql, "from": 0, "size": limit,
                    "start_time": int(start.timestamp() * 1_000_000),
                    "end_time": int(end.timestamp() * 1_000_000),
                }
            },
            timeout=cfg.timeout_seconds,
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
            raise ValueError("Unexpected OpenObserve search response")
        return payload["hits"][:limit]


class OpenObserveTraceLogs(Tool):
    def __init__(self, toolset: OpenObserveToolset):
        super().__init__(
            name="openobserve_trace_logs",
            description="Retrieve log evidence for a specific trace ID from the configured log stream. Read-only; never execute arbitrary SQL.",
            parameters={
                "trace_id": ToolParameter(description="Existing application Trace ID", type="string", required=True),
                "minutes": ToolParameter(description="Lookback window, 1-1440 minutes (default 30)", type="integer", required=False),
                "limit": ToolParameter(description="Maximum returned records, 1-100 (default 25)", type="integer", required=False),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            hits = self._toolset.search_trace(
                params["trace_id"], params.get("minutes", 30), params.get("limit", 25)
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS if hits else StructuredToolResultStatus.NO_DATA,
                data=hits, params=params,
            )
        except (KeyError, ValueError, requests.RequestException) as exc:
            # Never emit API credentials or untrusted server response bodies.
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"OpenObserve search failed: {type(exc).__name__}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"OpenObserve: query trace {params.get('trace_id', '<missing>')}"
