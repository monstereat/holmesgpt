import base64
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import ProxyHandler, validate_search


def search_body(sql="SELECT * FROM app_logs", *, start=1_000_000, end=2_000_000, size=20):
    return {
        "query": {"sql": sql, "start_time": start, "end_time": end, "from": 0, "size": size},
        "search_type": "ui",
        "timeout": 10,
    }


class ProxyPolicyTests(unittest.TestCase):
    def test_accepts_allowlisted_select_and_clamps_rows(self):
        result = validate_search(search_body(size=500))
        self.assertEqual(result["query"]["size"], 100)
        self.assertEqual(result["query"]["from"], 0)

    def test_rejects_other_streams_and_database_qualification(self):
        for sql in ("SELECT * FROM secrets", "SELECT * FROM default.app_logs"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_search(search_body(sql))

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
                validate_search(search_body(sql))

    def test_rejects_invalid_window_shape_and_timeout(self):
        invalid = (
            search_body(start=0),
            search_body(start=1, end=3_600_000_002),
            search_body(end=1_000_000),
            search_body(end=time.time_ns() // 1_000 + 61_000_000),
        )
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_search(body)
        body = search_body()
        body["timeout"] = 31
        with self.assertRaises(ValueError):
            validate_search(body)

    def test_rejects_unknown_request_fields(self):
        body = search_body()
        body["organization"] = "other"
        with self.assertRaises(ValueError):
            validate_search(body)


class ProxyHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type(
            "TestProxyHandler",
            (ProxyHandler,),
            {"client_user": "holmes", "client_password": "proxy-secret"},
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
                {"name": "app_logs", "stream_type": "logs", "schema": ["message"]},
                {"name": "other", "schema": ["private"]},
            ]
        }
        with patch("app._upstream_request", return_value=(200, json.dumps(payload).encode())) as upstream:
            status, result = self.request("GET", "/api/default/streams?fetchSchema=true&type=logs")
        self.assertEqual(status, 200)
        self.assertEqual([item["name"] for item in result["list"]], ["app_logs"])
        upstream.assert_called_once_with(
            "GET", "/api/default/streams?fetchSchema=true&type=logs", None
        )

    def test_invalid_search_never_reaches_upstream(self):
        with patch("app._upstream_request") as upstream:
            status, _ = self.request("POST", "/api/default/_search", body=search_body("SELECT * FROM secret"))
        self.assertEqual(status, 400)
        upstream.assert_not_called()

    def test_search_request_is_sanitized_before_forwarding(self):
        upstream_result = {"hits": [{"n": index} for index in range(110)], "total": 110, "took": 1}
        with patch("app._upstream_request", return_value=(200, json.dumps(upstream_result).encode())) as upstream:
            status, result = self.request("POST", "/api/default/_search", body=search_body(size=500))
        self.assertEqual(status, 200)
        self.assertEqual(len(result["hits"]), 100)
        forwarded = json.loads(upstream.call_args.args[2])
        self.assertEqual(forwarded["query"]["size"], 100)

    def test_upstream_error_body_is_not_forwarded(self):
        with patch("app._upstream_request", return_value=(500, b'{"error":"sensitive upstream detail"}')):
            status, result = self.request("GET", "/api/default/streams?fetchSchema=false&type=logs")
        self.assertEqual(status, 502)
        self.assertNotIn("sensitive upstream detail", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
