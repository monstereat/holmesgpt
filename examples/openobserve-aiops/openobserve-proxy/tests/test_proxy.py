import base64
import http.client
import json
import os
import threading
import time
import unittest
from unittest.mock import Mock
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import (
    ProxyHandler,
    _filter_record,
    _upstream_request,
    _upstream_settings,
    load_field_allowlists,
    main,
    validate_search,
)


FIELD_ALLOWLISTS = {
    "app_logs": (
        "_timestamp", "timestamp", "trace_id", "span_id", "service", "service_name", "level", "message",
        "route", "release", "event_type", "commit_sha", "changed_files", "deployed_at", "evaluation_run_id",
        "evaluation_case_id", "evaluation_source", "alert_name",
    ),
    "frontend_errors": (
        "_timestamp", "trace_id", "span_id", "service", "release", "message", "stack", "route",
    ),
}


def search_body(sql="SELECT * FROM app_logs", *, start=1_000_000, end=2_000_000, size=20):
    return {
        "query": {"sql": sql, "start_time": start, "end_time": end, "from": 0, "size": size},
        "search_type": "ui",
        "timeout": 10,
    }


class ProxyPolicyTests(unittest.TestCase):
    def test_accepts_allowlisted_select_and_clamps_rows(self):
        result = validate_search(search_body(size=500), FIELD_ALLOWLISTS)
        self.assertEqual(result["query"]["size"], 100)
        self.assertEqual(result["query"]["from"], 0)
        self.assertIn("evaluation_run_id", result["query"]["sql"])
        self.assertIn("evaluation_case_id", result["query"]["sql"])
        self.assertIn("changed_files", result["query"]["sql"])
        self.assertNotIn("user_id", result["query"]["sql"])

    def test_accepts_holmes_find_trace_query_shape(self):
        trace_id = "a" * 32
        sql = f'SELECT * FROM "app_logs" WHERE trace_id = \'{trace_id}\' ORDER BY _timestamp DESC'
        result = validate_search(search_body(sql), FIELD_ALLOWLISTS)
        self.assertIn("trace_id", result["query"]["sql"])
        self.assertIn("_timestamp DESC", result["query"]["sql"])

    def test_accepts_scoped_evaluation_predicates_joined_with_and(self):
        sql = (
            "SELECT * FROM app_logs WHERE trace_id = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "
            "AND evaluation_run_id = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' "
            "AND evaluation_case_id = 'order-inventory-negative-stock' ORDER BY _timestamp ASC"
        )
        result = validate_search(search_body(sql), FIELD_ALLOWLISTS)
        self.assertIn("evaluation_run_id", result["query"]["sql"])
        self.assertIn("evaluation_case_id", result["query"]["sql"])

    def test_rejects_or_predicates_and_sql_functions(self):
        queries = (
            "SELECT message FROM app_logs WHERE service = 'api' OR level = 'error'",
            "SELECT count(*) FROM app_logs WHERE evaluation_run_id = 'run' AND evaluation_case_id = 'case'",
        )
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_search(search_body(sql), FIELD_ALLOWLISTS)

    def test_rejects_disallowed_selected_or_filtered_fields(self):
        for sql in (
            "SELECT user_id FROM app_logs",
            "SELECT message FROM app_logs WHERE user_id = 'private'",
            "SELECT message AS user_id FROM app_logs",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_search(search_body(sql), FIELD_ALLOWLISTS)

    def test_rejects_qualified_columns_and_nested_value_extraction(self):
        expanded_policy = {
            **FIELD_ALLOWLISTS,
            "app_logs": (*FIELD_ALLOWLISTS["app_logs"], "metadata"),
        }
        queries = (
            "SELECT secret_db.app_logs.message FROM app_logs",
            "SELECT other_catalog.secret_db.app_logs.message FROM app_logs",
            "SELECT frontend_errors.message FROM app_logs",
            "SELECT metadata['token'] AS message FROM app_logs",
            "SELECT JSONExtractString(metadata, 'token') AS message FROM app_logs",
        )
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_search(search_body(sql), expanded_policy)
        self.assertIn(
            "app_logs.message",
            validate_search(search_body("SELECT app_logs.message FROM app_logs"), FIELD_ALLOWLISTS)["query"]["sql"],
        )

    def test_loads_strict_per_stream_field_policy(self):
        raw = json.dumps({stream: list(fields) for stream, fields in FIELD_ALLOWLISTS.items()})
        self.assertEqual(load_field_allowlists(raw), FIELD_ALLOWLISTS)
        invalid = (
            "not-json",
            json.dumps({"app_logs": ["message"]}),
            json.dumps({"app_logs": ["message", "message"], "frontend_errors": ["message"]}),
            json.dumps({"app_logs": ["*"], "frontend_errors": ["message"]}),
            json.dumps({"app_logs": [f"field{index}" for index in range(101)], "frontend_errors": ["message"]}),
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                load_field_allowlists(value)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            RuntimeError, "OPENOBSERVE_ALLOWED_FIELDS_JSON"
        ):
            load_field_allowlists()

    def test_startup_fails_before_binding_without_field_policy(self):
        settings = {
            "AIOPS_ENV": "production",
            "OPENOBSERVE_UPSTREAM_URL": "https://observe.example",
            "OPENOBSERVE_ORG": "default",
            "OPENOBSERVE_UPSTREAM_USERNAME": "reader",
            "OPENOBSERVE_UPSTREAM_PASSWORD": "reader-secret",
            "OPENOBSERVE_PROXY_USERNAME": "holmes",
            "OPENOBSERVE_PROXY_PASSWORD": "proxy-secret",
        }
        with patch.dict(os.environ, settings, clear=True), patch("app.ThreadingHTTPServer") as server:
            with self.assertRaisesRegex(RuntimeError, "OPENOBSERVE_ALLOWED_FIELDS_JSON"):
                main()
            server.assert_not_called()

    def test_field_filter_drops_unlisted_and_nested_object_values(self):
        result = _filter_record(
            {
                "message": "approved scalar field",
                "private": "secret",
                "metadata": {"message": "nested sensitive value"},
                "changed_files": ["src/app.py"],
                "items": [{"message": "nested value"}],
            },
            {"message", "metadata", "changed_files", "items"},
        )
        self.assertEqual(result, {"message": "approved scalar field", "changed_files": ["src/app.py"]})

    def test_rejects_other_streams_and_database_qualification(self):
        for sql in ("SELECT * FROM secrets", "SELECT * FROM default.app_logs"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_search(search_body(sql), FIELD_ALLOWLISTS)

    def test_rejects_compound_nested_and_external_queries(self):
        queries = (
            "SELECT * FROM app_logs JOIN frontend_errors ON 1=1",
            "SELECT * FROM app_logs UNION SELECT * FROM frontend_errors",
            "SELECT * FROM (SELECT * FROM app_logs)",
            "SELECT * FROM remote('host', 'db', 'table')",
            "SELECT * FROM app_logs; DROP TABLE app_logs",
            "SELECT * FROM app_logs -- comment",
            "SELECT evil_fn(*) FROM app_logs",
        )
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_search(search_body(sql), FIELD_ALLOWLISTS)

    def test_rejects_invalid_window_shape_and_timeout(self):
        invalid = (
            search_body(start=0),
            search_body(start=1, end=3_600_000_002),
            search_body(end=1_000_000),
            search_body(end=time.time_ns() // 1_000 + 61_000_000),
        )
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_search(body, FIELD_ALLOWLISTS)
        body = search_body()
        body["timeout"] = 31
        with self.assertRaises(ValueError):
            validate_search(body, FIELD_ALLOWLISTS)

    def test_rejects_unknown_request_fields(self):
        body = search_body()
        body["organization"] = "other"
        with self.assertRaises(ValueError):
            validate_search(body, FIELD_ALLOWLISTS)

    def test_upstream_requires_https_outside_local_mode(self):
        settings = {
            "OPENOBSERVE_UPSTREAM_URL": "http://openobserve:5080",
            "OPENOBSERVE_ORG": "default",
            "OPENOBSERVE_UPSTREAM_USERNAME": "reader",
            "OPENOBSERVE_UPSTREAM_PASSWORD": "secret",
            "AIOPS_ENV": "production",
        }
        with patch.dict(os.environ, settings, clear=True), self.assertRaises(RuntimeError):
            _upstream_settings()

    def test_upstream_rejects_credentials_and_paths_in_url(self):
        settings = {
            "OPENOBSERVE_UPSTREAM_URL": "https://reader:secret@observe.example/api",
            "OPENOBSERVE_ORG": "default",
            "OPENOBSERVE_UPSTREAM_USERNAME": "reader",
            "OPENOBSERVE_UPSTREAM_PASSWORD": "secret",
            "AIOPS_ENV": "production",
        }
        with patch.dict(os.environ, settings, clear=True), self.assertRaises(RuntimeError):
            _upstream_settings()

    def test_upstream_rejects_invalid_organization_path(self):
        settings = {
            "OPENOBSERVE_UPSTREAM_URL": "https://observe.example",
            "OPENOBSERVE_ORG": "tenant-a/../../users",
            "OPENOBSERVE_UPSTREAM_USERNAME": "reader",
            "OPENOBSERVE_UPSTREAM_PASSWORD": "secret",
            "AIOPS_ENV": "production",
        }
        with patch.dict(os.environ, settings, clear=True), self.assertRaises(RuntimeError):
            _upstream_settings()

    def test_upstream_uses_tls_and_dedicated_credentials(self):
        settings = {
            "OPENOBSERVE_UPSTREAM_URL": "https://observe.example:8443",
            "OPENOBSERVE_ORG": "tenant-a",
            "OPENOBSERVE_UPSTREAM_USERNAME": "reader",
            "OPENOBSERVE_UPSTREAM_PASSWORD": "read-secret",
            "AIOPS_ENV": "production",
        }
        response = Mock(status=200)
        response.read.return_value = b'{"list": []}'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.dict(os.environ, settings, clear=True), patch(
            "app.http.client.HTTPSConnection", return_value=connection
        ) as https_connection:
            status, body = _upstream_request("GET", "/api/tenant-a/streams")
        self.assertEqual((status, body), (200, b'{"list": []}'))
        https_connection.assert_called_once_with("observe.example", 8443, timeout=10)
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual(
            headers["Authorization"],
            "Basic " + base64.b64encode(b"reader:read-secret").decode(),
        )


class ProxyHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type(
            "TestProxyHandler",
            (ProxyHandler,),
            {
                "client_user": "holmes",
                "client_password": "proxy-secret",
                "field_allowlists": FIELD_ALLOWLISTS,
            },
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, *, body=None, auth=True, content_type="application/json"):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        headers = {"Content-Type": content_type}
        if auth:
            token = base64.b64encode(b"holmes:proxy-secret").decode()
            headers["Authorization"] = f"Basic {token}"
        payload = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_authentication_is_required_for_api_routes(self):
        status, _ = self.request("GET", "/api/default/streams?fetchSchema=false&type=logs", auth=False)
        self.assertEqual(status, 401)

    def test_unlisted_routes_are_not_forwarded(self):
        with patch("app._upstream_request") as upstream:
            status, _ = self.request("GET", "/api/default/users")
            self.assertEqual(status, 404)
            upstream.assert_not_called()

    def test_stream_listing_is_filtered_before_return(self):
        payload = {
            "list": [
                {"name": "app_logs", "stream_type": "logs", "schema": ["message", "user_id"]},
                {"name": "other", "schema": ["private"]},
            ]
        }
        with patch("app._upstream_request", return_value=(200, json.dumps(payload).encode())) as upstream:
            status, result = self.request("GET", "/api/default/streams?fetchSchema=true&type=logs")
        self.assertEqual(status, 200)
        self.assertEqual([item["name"] for item in result["list"]], ["app_logs"])
        self.assertEqual(result["list"][0]["schema"], ["message"])
        upstream.assert_called_once_with(
            "GET", "/api/default/streams?fetchSchema=true&type=logs", None
        )

    def test_invalid_search_never_reaches_upstream(self):
        with patch("app._upstream_request") as upstream:
            status, _ = self.request("POST", "/api/default/_search", body=search_body("SELECT * FROM secret"))
        self.assertEqual(status, 400)
        upstream.assert_not_called()

    def test_search_request_is_sanitized_before_forwarding(self):
        upstream_result = {
            "hits": [
                {"message": f"record-{index}", "user_id": "private", "details": {"trace_id": "trace", "token": "secret"}}
                for index in range(110)
            ],
            "total": 110,
            "took": 1,
        }
        with patch("app._upstream_request", return_value=(200, json.dumps(upstream_result).encode())) as upstream:
            status, result = self.request("POST", "/api/default/_search", body=search_body(size=500))
        self.assertEqual(status, 200)
        self.assertEqual(len(result["hits"]), 100)
        self.assertEqual(result["hits"][0], {"message": "record-0"})
        forwarded = json.loads(upstream.call_args.args[2])
        self.assertEqual(forwarded["query"]["size"], 100)
        self.assertIn("evaluation_run_id", forwarded["query"]["sql"])

    def test_malformed_search_rows_fail_closed(self):
        with patch("app._upstream_request", return_value=(200, b'{"hits":["unstructured"]}')):
            status, result = self.request("POST", "/api/default/_search", body=search_body())
        self.assertEqual(status, 502)
        self.assertEqual(result, {"error": "OpenObserve policy upstream failed"})

    def test_upstream_error_body_is_not_forwarded(self):
        with patch("app._upstream_request", return_value=(500, b'{"error":"sensitive upstream detail"}')):
            status, result = self.request("GET", "/api/default/streams?fetchSchema=false&type=logs")
        self.assertEqual(status, 502)
        self.assertNotIn("sensitive upstream detail", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
