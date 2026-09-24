import json
import re
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

import requests  # type: ignore[import-untyped]
from pydantic import Field, field_validator
from urllib.parse import urlsplit
from requests.auth import HTTPBasicAuth

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.utils.pydantic_utils import ToolsetConfig


FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge)\b",
    re.IGNORECASE,
)


class OpenObserveConfig(ToolsetConfig):
    api_url: str = Field(
        title="API URL",
        description="OpenObserve base URL, for example https://observe.example.com",
    )
    organization: str = Field(
        title="Organization",
        description="OpenObserve organization identifier",
    )
    username: str = Field(
        title="Service Account Email",
        description="OpenObserve service-account email or API username",
    )
    password: str = Field(
        title="Service Account Token",
        description="OpenObserve service-account token/password",
    )
    verify_ssl: bool = Field(default=True, title="Verify SSL")
    timeout_seconds: int = Field(default=30, ge=1, le=120, title="Timeout Seconds")
    max_window_seconds: int = Field(default=86400, ge=1, le=604800)
    allowed_streams: List[str] = Field(default_factory=list, description="Required for production: exact names of permitted log streams")
    max_rows: int = Field(
        default=200,
        ge=1,
        le=1000,
        title="Maximum Search Rows",
        description="Hard cap on rows returned by the LLM-facing search tool",
    )

    @field_validator("api_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or
                url.username or url.password or url.query or url.fragment):
            raise ValueError("Use a plain HTTP(S) OpenObserve API base URL without credentials or query")
        return value.rstrip("/")

    @field_validator("allowed_streams")
    @classmethod
    def validate_stream_allowlist(cls, streams: List[str]) -> List[str]:
        if len(streams) > 100 or any(
            not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name) for name in streams
        ):
            raise ValueError("Up to 100 simple OpenObserve stream names are allowed")
        return streams

    @field_validator("organization")
    @classmethod
    def validate_organization(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("Invalid OpenObserve organization identifier")
        return value


class OpenObserveToolset(Toolset):
    config_classes: ClassVar[list[Type[OpenObserveConfig]]] = [OpenObserveConfig]

    def __init__(self):
        super().__init__(
            name="openobserve",
            enabled=False,
            description=(
                "Read-only OpenObserve integration for incident investigation. "
                "Lists streams and searches logs with bounded SQL queries."
            ),
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [OpenObserveListStreams(self), OpenObserveSearchLogs(self), OpenObserveFindTrace(self)]

    @property
    def openobserve_config(self) -> OpenObserveConfig:
        return self.config  # type: ignore[return-value]

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "OpenObserve configuration is missing"
        try:
            self.config = OpenObserveConfig(**config)
            data = self._request(
                "GET",
                f"/api/{self.openobserve_config.organization}/streams",
                params={"fetchSchema": "false", "type": "logs"},
                timeout=min(self.openobserve_config.timeout_seconds, 10),
            )
            count = len(data.get("list", [])) if isinstance(data, dict) else 0
            return True, f"Connected to OpenObserve ({count} log streams visible)"
        except Exception as exc:
            return False, f"OpenObserve health check failed: {exc}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        cfg = self.openobserve_config
        url = f"{cfg.api_url.rstrip('/')}/{path.lstrip('/')}"
        response = requests.request(
            method,
            url,
            auth=HTTPBasicAuth(cfg.username, cfg.password),
            headers={"Accept": "application/json"},
            params=params,
            json=json_body,
            timeout=timeout or cfg.timeout_seconds,
            verify=cfg.verify_ssl,
            allow_redirects=False,  # Never forward a service token to a redirect target.
        )
        if isinstance(response.status_code, int) and 300 <= response.status_code < 400:
            raise ValueError('OpenObserve API returned an unexpected redirect')
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("OpenObserve returned a non-object JSON response")
        return value


class _BaseOpenObserveTool(Tool, ABC):
    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        """Human-readable progress line required by the base Tool interface."""
        return f"OpenObserve: {self.name}"

    def __init__(self, toolset: OpenObserveToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _error(self, params: dict, operation: str, exc: Exception) -> StructuredToolResult:
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            # OpenObserve may echo authorization headers or SQL literals in
            # error bodies. Preserve status without forwarding the raw body.
            detail = f"HTTP {exc.response.status_code} (upstream response redacted)"
        else:
            detail = str(exc)
            if isinstance(exc, requests.exceptions.RequestException):
                detail = type(exc).__name__ + " (upstream details redacted)"
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"OpenObserve {operation} failed. {detail}",
            params=params,
        )


class OpenObserveListStreams(_BaseOpenObserveTool):
    def __init__(self, toolset: OpenObserveToolset):
        super().__init__(
            toolset=toolset,
            name="openobserve_list_log_streams",
            description=(
                "List visible OpenObserve log streams. Use this before writing SQL so "
                "you know the exact stream names."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset._request(
                "GET",
                f"/api/{self._toolset.openobserve_config.organization}/streams",
                params={"fetchSchema": "true", "type": "logs"},
            )
            streams = []
            allowed = set(self._toolset.openobserve_config.allowed_streams)
            for item in data.get("list", []):
                if not isinstance(item, dict):
                    continue
                if allowed and item.get("name") not in allowed:
                    continue
                if len(streams) >= 100:
                    break
                streams.append(
                    {
                        "name": item.get("name"),
                        "stream_type": item.get("stream_type"),
                        "stats": item.get("stats"),
                        "schema": item.get("schema", [])[:100],
                    }
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data={"streams": streams},
                params=params,
            )
        except Exception as exc:
            return self._error(params, "stream listing", exc)


class OpenObserveSearchLogs(_BaseOpenObserveTool):
    def __init__(self, toolset: OpenObserveToolset):
        super().__init__(
            toolset=toolset,
            name="openobserve_search_logs",
            description=(
                "Run a read-only SQL log search in OpenObserve for an explicit time range. "
                "Only SELECT/WITH queries are accepted and result size is bounded."
            ),
            parameters={
                "sql": ToolParameter(
                    description="Read-only OpenObserve SQL. Must start with SELECT or WITH.",
                    type="string",
                    required=True,
                ),
                "start_time": ToolParameter(
                    description="Search start time as Unix epoch microseconds.",
                    type="integer",
                    required=True,
                ),
                "end_time": ToolParameter(
                    description="Search end time as Unix epoch microseconds.",
                    type="integer",
                    required=True,
                ),
                "size": ToolParameter(
                    description="Maximum rows to return. Server-side capped by configuration.",
                    type="integer",
                    required=False,
                ),
            },
        )

    @staticmethod
    def validate_sql(sql: str) -> str:
        candidate = sql.strip()
        if not candidate or ";" in candidate:
            raise ValueError("SQL must be one statement without semicolons")
        if not re.match(r"^(select|with)\b", candidate, re.IGNORECASE):
            raise ValueError("Only SELECT/WITH SQL is allowed")
        if FORBIDDEN_SQL.search(candidate):
            raise ValueError("Mutating or administrative SQL is not allowed")
        return candidate

    @staticmethod
    def validate_stream_scope(sql: str, allowlist: List[str]) -> None:
        if not allowlist:
            return  # Development-only. Production must configure allowed_streams.
        # Fail closed: the strict mode permits one simple stream, not arbitrary
        # CTEs, subqueries, UNIONs, comments, or JOINs. Rich queries require
        # a real dialect-aware parser and database-side least-privilege access.
        if re.search(r"--|/\*|\*/|\b(with|union|join|intersect|except)\b", sql, re.I):
            raise ValueError("Complex SQL is not permitted by the stream allowlist")
        if len(re.findall(r"\bselect\b", sql, re.I)) != 1:
            raise ValueError("Strict search permits exactly one SELECT")
        matches = re.findall(
            r'\bfrom\s+(?:"([A-Za-z0-9_]{1,64})"|([A-Za-z0-9_]{1,64}))(?=\s*(?:where\b|group\s+by\b|order\s+by\b|limit\b|$))',
            sql, re.I,
        )
        if len(matches) != 1:
            raise ValueError("Strict search must query exactly one named stream")
        stream = matches[0][0] or matches[0][1]
        if stream not in allowlist:
            raise ValueError("Log stream is not in the configured allowlist")

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            sql = self.validate_sql(str(params["sql"]))
            self.validate_stream_scope(sql, self._toolset.openobserve_config.allowed_streams)
            start_time = int(params["start_time"])
            end_time = int(params["end_time"])
            if start_time <= 0 or end_time <= 0 or end_time <= start_time:
                raise ValueError("end_time must be greater than start_time")
            if end_time - start_time > self._toolset.openobserve_config.max_window_seconds * 1_000_000:
                raise ValueError("Search time range exceeds the configured limit")
            requested_size = int(params.get("size") or 50)
            if requested_size < 1:
                raise ValueError("size must be positive")
            size = min(requested_size, self._toolset.openobserve_config.max_rows)

            payload = {
                "query": {
                    "sql": sql,
                    "start_time": start_time,
                    "end_time": end_time,
                    "from": 0,
                    "size": size,
                },
                "search_type": "ui",
                "timeout": self._toolset.openobserve_config.timeout_seconds,
            }
            data = self._toolset._request(
                "POST",
                f"/api/{self._toolset.openobserve_config.organization}/_search",
                json_body=payload,
            )
            hits = data.get("hits", [])
            if not isinstance(hits, list):
                hits = []
            result = {
                "hits": hits[:size],
                "total": data.get("total"),
                "took": data.get("took"),
                "scan_size": data.get("scan_size"),
                "size": size,
                "query": {
                    "sql": sql,
                    "start_time": start_time,
                    "end_time": end_time,
                },
            }
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
            )
        except Exception as exc:
            return self._error(params, "log search", exc)


class OpenObserveFindTrace(_BaseOpenObserveTool):
    """Investigate a trace ID in one explicitly named log stream."""

    def __init__(self, toolset: OpenObserveToolset):
        super().__init__(
            toolset=toolset,
            name="openobserve_find_trace",
            description=(
                "Find logs for an explicit trace_id in one OpenObserve log stream. "
                "Use this to correlate frontend errors with backend requests."
            ),
            parameters={
                "stream": ToolParameter(
                    description="Exact log stream name returned by the list-streams tool",
                    type="string",
                    required=True,
                ),
                "trace_id": ToolParameter(
                    description="W3C 32-hex-digit trace ID",
                    type="string",
                    required=True,
                ),
                "start_time": ToolParameter(
                    description="Start time (Unix microseconds)",
                    type="integer",
                    required=True,
                ),
                "end_time": ToolParameter(
                    description="End time (Unix microseconds)",
                    type="integer",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            stream = str(params["stream"])
            trace_id = str(params["trace_id"])
            if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", stream):
                raise ValueError("Invalid stream name")
            if not re.fullmatch(r"[a-fA-F0-9]{32}", trace_id):
                raise ValueError("trace_id must be 32 hexadecimal characters")
            allowed = self._toolset.openobserve_config.allowed_streams
            if allowed and stream not in allowed:
                raise ValueError("Log stream is not in the configured allowlist")
            sql = (
                f'SELECT * FROM "{stream}" WHERE trace_id = '
                f"'{trace_id.lower()}' ORDER BY _timestamp DESC"
            )
            return OpenObserveSearchLogs(self._toolset)._invoke(
                {
                    "sql": sql,
                    "start_time": params["start_time"],
                    "end_time": params["end_time"],
                    "size": min(self._toolset.openobserve_config.max_rows, 100),
                },
                context,
            )
        except (ValueError, KeyError, TypeError) as exc:
            return self._error(params, "trace lookup", exc)
