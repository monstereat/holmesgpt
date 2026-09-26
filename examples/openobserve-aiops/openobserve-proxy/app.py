"""Narrow, authenticated read-only gateway for the local OpenObserve OSS demo."""

from __future__ import annotations

import base64
import hmac
import http.client
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


ORGANIZATION = "default"
ALLOWED_STREAMS = frozenset({"app_logs", "frontend_errors"})
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304
MAX_ROWS = 100
MAX_WINDOW_US = 3_600 * 1_000_000
MAX_TIMEOUT_SECONDS = 30
SQL_COMMENT_MARKERS = re.compile(r"--|/\*|\*/|#|;")
FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _upstream_settings() -> tuple[str, str, str, str, int]:
    raw_url = _required_env("OPENOBSERVE_UPSTREAM_URL").rstrip("/")
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("OPENOBSERVE_UPSTREAM_URL must be an http(s) origin without credentials or path")
    if parsed.scheme != "https" and os.getenv("AIOPS_ENV") != "local":
        raise RuntimeError("OPENOBSERVE_UPSTREAM_URL must use HTTPS outside local mode")
    organization = _required_env("OPENOBSERVE_ORG")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", organization):
        raise RuntimeError("OPENOBSERVE_ORG is invalid")
    username = _required_env("OPENOBSERVE_UPSTREAM_USERNAME")
    password = _required_env("OPENOBSERVE_UPSTREAM_PASSWORD")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, organization, username, port


def load_field_allowlists(raw: str | None = None) -> dict[str, tuple[str, ...]]:
    configured = raw if raw is not None else _required_env("OPENOBSERVE_ALLOWED_FIELDS_JSON")
    try:
        value = json.loads(configured)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OPENOBSERVE_ALLOWED_FIELDS_JSON must be valid JSON") from exc
    if not isinstance(value, dict) or set(value) != ALLOWED_STREAMS:
        raise RuntimeError("OPENOBSERVE_ALLOWED_FIELDS_JSON must define every allowed stream exactly once")

    allowlists: dict[str, tuple[str, ...]] = {}
    for stream, fields in value.items():
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(field, str) or not FIELD_NAME.fullmatch(field) for field in fields)
            or len(fields) != len(set(fields))
        ):
            raise RuntimeError("OPENOBSERVE_ALLOWED_FIELDS_JSON contains an invalid field list")
        allowlists[stream] = tuple(fields)
    return allowlists


def _column_name(column: exp.Column, source_stream: str) -> str:
    if column.db or column.catalog or (column.table and column.table != source_stream):
        raise ValueError("Qualified columns are not permitted")
    return column.name


def _contains_object(value: Any) -> bool:
    if isinstance(value, dict):
        return True
    return isinstance(value, list) and any(_contains_object(item) for item in value)


def _filter_record(value: dict[str, Any], allowed_fields: set[str]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key in allowed_fields and not _contains_object(item)
    }


def validate_search(
    body: dict[str, Any], field_allowlists: Mapping[str, tuple[str, ...]]
) -> dict[str, Any]:
    if set(body) != {"query", "search_type", "timeout"}:
        raise ValueError("Invalid search request shape")
    if body.get("search_type") != "ui":
        raise ValueError("Unsupported search type")
    timeout = body.get("timeout")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError("Invalid timeout")

    query = body.get("query")
    expected = {"sql", "start_time", "end_time", "from", "size"}
    if not isinstance(query, dict) or set(query) != expected:
        raise ValueError("Invalid query shape")
    sql = query.get("sql")
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 16_384:
        raise ValueError("Invalid SQL")
    if SQL_COMMENT_MARKERS.search(sql):
        raise ValueError("SQL comments and statement separators are not permitted")

    try:
        expression = parse_one(sql, dialect="clickhouse")
    except ParseError as exc:
        raise ValueError("Invalid SQL syntax") from exc
    if not isinstance(expression, exp.Select):
        raise ValueError("Only one SELECT statement is permitted")
    if any(
        isinstance(node, (exp.Subquery, exp.Join, exp.With, exp.Union, exp.Intersect, exp.Except))
        for node in expression.walk()
    ):
        raise ValueError("Nested or compound queries are not permitted")
    tables = list(expression.find_all(exp.Table))
    if len(tables) != 1:
        raise ValueError("Exactly one allowlisted stream is required")
    table = tables[0]
    if table.name not in ALLOWED_STREAMS or table.db or table.catalog or table.alias:
        raise ValueError("Log stream is not allowed")
    if any(isinstance(node, (exp.Func, exp.Bracket)) for node in expression.walk()):
        raise ValueError("SQL functions and nested field access are not permitted")

    allowed_fields = field_allowlists.get(table.name)
    if not allowed_fields:
        raise ValueError("Log stream has no configured field policy")
    projections = expression.expressions
    expanded_projections: list[exp.Expression] = []
    for projection in projections:
        if isinstance(projection, exp.Star) or (isinstance(projection, exp.Column) and projection.is_star):
            expanded_projections.extend(exp.column(field) for field in allowed_fields)
        else:
            if any(isinstance(node, exp.Star) or (isinstance(node, exp.Column) and node.is_star) for node in projection.walk()):
                raise ValueError("Wildcard expressions are not permitted")
            output_name = projection.alias_or_name
            if output_name and output_name not in allowed_fields:
                raise ValueError("Selected field is not allowed")
            expanded_projections.append(projection)
    expression.set("expressions", expanded_projections)

    for column in expression.find_all(exp.Column):
        if column.is_star:
            raise ValueError("Wildcard expressions are not permitted")
        if _column_name(column, table.name) not in allowed_fields:
            raise ValueError("Query references a field that is not allowed")

    start_time = query.get("start_time")
    end_time = query.get("end_time")
    if (
        isinstance(start_time, bool)
        or isinstance(end_time, bool)
        or not isinstance(start_time, int)
        or not isinstance(end_time, int)
        or start_time <= 0
        or end_time <= start_time
        or end_time - start_time > MAX_WINDOW_US
        or end_time > time.time_ns() // 1_000 + 60_000_000
    ):
        raise ValueError("Invalid or overlong time range")
    offset = query.get("from")
    size = query.get("size")
    if isinstance(offset, bool) or offset != 0:
        raise ValueError("Pagination offset is not allowed")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError("Invalid result size")

    # Send SQL regenerated from the parsed AST so comments, separators, and
    # parser-ignored suffixes never reach the upstream query engine.
    return {
        "query": {
            "sql": expression.sql(dialect="clickhouse"),
            "start_time": start_time,
            "end_time": end_time,
            "from": 0,
            "size": min(size, MAX_ROWS),
        },
        "search_type": "ui",
        "timeout": timeout,
    }


def _upstream_request(method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    scheme, host, _organization, username, port = _upstream_settings()
    password = _required_env("OPENOBSERVE_UPSTREAM_PASSWORD")
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Accept": "application/json",
        "Connection": "close",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))

    connection_type = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    connection = connection_type(host, port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("Upstream response exceeded the size limit")
        if 300 <= response.status < 400:
            raise ValueError("Upstream redirect rejected")
        return response.status, payload
    finally:
        connection.close()


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "AIOpsOpenObservePolicy/1.0"
    client_user = ""
    client_password = ""
    organization = ORGANIZATION
    field_allowlists: Mapping[str, tuple[str, ...]] = {}

    def log_message(self, format: str, *args: Any) -> None:
        # Keep SQL, query parameters and upstream details out of container logs.
        super().log_message("request from %s", self.client_address[0])

    def _reply(self, status: int, value: dict[str, Any], extra_headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, header_value in (extra_headers or {}).items():
            self.send_header(key, header_value)
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        if not self.client_user or not self.client_password:
            return False
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "basic" or not token:
            return False
        try:
            supplied = base64.b64decode(token, validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        expected = f"{self.client_user}:{self.client_password}"
        return hmac.compare_digest(supplied, expected)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._reply(401, {"error": "unauthorized"}, {"WWW-Authenticate": 'Basic realm="openobserve-proxy"'})
        return False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler interface
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz" and not parsed.query:
            self._reply(200, {"status": "healthy"})
            return
        if not self._require_auth():
            return
        if parsed.path != f"/api/{self.organization}/streams":
            self._reply(404, {"error": "route not allowed"})
            return
        try:
            params = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
        except ValueError:
            self._reply(400, {"error": "invalid query parameters"})
            return
        if set(params) != {"fetchSchema", "type"} or any(len(values) != 1 for values in params.values()):
            self._reply(400, {"error": "invalid query parameters"})
            return
        if params["type"][0] != "logs" or params["fetchSchema"][0] not in {"true", "false"}:
            self._reply(400, {"error": "invalid query parameters"})
            return
        upstream_path = f"/api/{self.organization}/streams?fetchSchema={params['fetchSchema'][0]}&type=logs"
        self._forward("GET", upstream_path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler interface
        parsed = urlsplit(self.path)
        if not self._require_auth():
            return
        if parsed.path != f"/api/{self.organization}/_search" or parsed.query:
            self._reply(404, {"error": "route not allowed"})
            return
        if self.headers.get_content_type() != "application/json":
            self._reply(415, {"error": "application/json required"})
            return
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit() or len(raw_length) > 7 or int(raw_length) > MAX_REQUEST_BYTES:
            self._reply(413, {"error": "request too large"})
            return
        try:
            self.connection.settimeout(10)
            raw_body = self.rfile.read(int(raw_length))
            value = json.loads(raw_body)
            if not isinstance(value, dict):
                raise ValueError("Invalid search request")
            sanitized = validate_search(value, self.field_allowlists)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._reply(400, {"error": str(exc)})
            return
        self._forward(
            "POST",
            f"/api/{self.organization}/_search",
            json.dumps(sanitized, separators=(",", ":")).encode(),
        )

    def do_PUT(self) -> None:  # noqa: N802
        self._reply(405, {"error": "method not allowed"}, {"Allow": "GET, POST"})

    do_PATCH = do_PUT
    do_DELETE = do_PUT
    do_OPTIONS = do_PUT

    def _forward(self, method: str, path: str, body: bytes | None = None) -> None:
        try:
            status, payload = _upstream_request(method, path, body)
            if status >= 400:
                self._reply(502, {"error": "OpenObserve policy upstream failed"})
                return
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("Invalid upstream response")
            if method == "GET":
                streams = data.get("list")
                if not isinstance(streams, list):
                    raise ValueError("Invalid upstream stream list")
                filtered = []
                for item in streams:
                    if not isinstance(item, dict) or item.get("name") not in ALLOWED_STREAMS:
                        continue
                    stream_name = item["name"]
                    allowed_fields = set(self.field_allowlists.get(stream_name, ()))
                    schema = item.get("schema", [])
                    filtered_schema = []
                    if isinstance(schema, list):
                        for field in schema[:100]:
                            name = field if isinstance(field, str) else field.get("name") if isinstance(field, dict) else None
                            if name in allowed_fields:
                                filtered_schema.append(
                                    {"name": name, "type": field.get("type")}
                                    if isinstance(field, dict)
                                    else name
                                )
                    filtered.append(
                        {
                            "name": stream_name,
                            "stream_type": item.get("stream_type"),
                            "stats": item.get("stats"),
                            "schema": filtered_schema,
                        }
                    )
                data = {"list": filtered}
            else:
                hits = data.get("hits", [])
                if not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
                    raise ValueError("Invalid upstream search response")
                request = json.loads(body or b"{}")
                limit = request["query"]["size"]
                tables = list(parse_one(request["query"]["sql"], dialect="clickhouse").find_all(exp.Table))
                allowed_fields = set(self.field_allowlists[tables[0].name])
                data = {
                    "hits": [
                        _filter_record(hit, allowed_fields)
                        for hit in hits[:limit]
                    ],
                    "total": data.get("total"),
                    "took": data.get("took"),
                    "scan_size": data.get("scan_size"),
                }
            response_body = json.dumps(data, separators=(",", ":")).encode()
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise ValueError("Response exceeded the size limit")
            self._reply(status, data)
        except (OSError, http.client.HTTPException, ValueError, json.JSONDecodeError):
            self._reply(502, {"error": "OpenObserve policy upstream failed"})


def main() -> None:
    _scheme, _host, organization, _username, _port = _upstream_settings()
    field_allowlists = load_field_allowlists()
    handler = type(
        "ConfiguredProxyHandler",
        (ProxyHandler,),
        {
            "client_user": _required_env("OPENOBSERVE_PROXY_USERNAME"),
            "client_password": _required_env("OPENOBSERVE_PROXY_PASSWORD"),
            "organization": organization,
            "field_allowlists": field_allowlists,
        },
    )
    server = ThreadingHTTPServer(("0.0.0.0", 8090), handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
