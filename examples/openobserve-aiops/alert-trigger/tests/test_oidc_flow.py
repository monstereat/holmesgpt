import base64
import asyncio
import hashlib
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import psycopg
import pytest

from fastapi.testclient import TestClient
from starlette.requests import Request

import app as app_module
from auth import create_session
from migration_runner import apply_migrations
from models import Principal


class FakeOIDCClient:
    def __init__(self, issuer):
        self.issuer = issuer
        self.authorization_params = None
        self.token_params = None

    async def load_server_metadata(self):
        return {
            "issuer": self.issuer,
            "authorization_endpoint": "https://id.example.com/authorize",
            "token_endpoint": "https://id.example.com/token",
            "code_challenge_methods_supported": ["S256"],
        }

    async def create_authorization_url(self, redirect_uri=None, **params):
        self.authorization_params = params
        return {"url": f"https://id.example.com/authorize?state={params['state']}&redirect_uri={redirect_uri}", "state": params["state"]}

    async def fetch_token(self, endpoint, **params):
        self.token_params = params
        return {"id_token": "signed-id-token"}

    async def parse_id_token(self, token, *, nonce):
        assert token["id_token"] == "signed-id-token"
        assert nonce == "saved-nonce"
        return {
            "iss": self.issuer,
            "sub": "subject-7",
            "preferred_username": "ops-user",
            "groups": ["aiops-operators"],
        }


class FakeCursor:
    def __init__(self, result):
        self.result = result
        self.results = iter(result) if isinstance(result, list) else None
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchone(self):
        if self.results is not None:
            return next(self.results, None)
        return self.result


class FakeConnection:
    def __init__(self, result):
        self.cursor_instance = FakeCursor(result)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        return self


def configure_oidc(monkeypatch):
    values = {
        "AIOPS_ENV": "local",
        "AIOPS_AUTH_MODE": "oidc",
        "DATABASE_URL": "postgresql://localhost/aiops",
        "SESSION_SIGNING_KEY": "test-only-session-signing-key-not-for-runtime",
        "SESSION_COOKIE_SECURE": "false",
        "OIDC_ISSUER": "https://id.example.com/tenant",
        "OIDC_METADATA_URL": "https://id.example.com/tenant/.well-known/openid-configuration",
        "OIDC_CLIENT_ID": "aiops-workbench",
        "OIDC_CLIENT_SECRET": "private-test-client-secret",
        "OIDC_REDIRECT_URI": "https://aiops.example.com/auth/oidc/callback",
        "AIOPS_PUBLIC_ORIGIN": "https://aiops.example.com",
        "OIDC_GROUP_MAPPINGS_JSON": '{"aiops-operators":{"role":"operator","resource_scopes":["order-service"]}}',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_oidc_authorization_and_callback_use_bound_state_pkce_and_http_only_session(monkeypatch):
    configure_oidc(monkeypatch)
    fake = FakeOIDCClient("https://id.example.com/tenant")
    monkeypatch.setattr(app_module, "_oidc_client", lambda _settings: fake)
    connections = [
        FakeConnection((0, 1)),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection(("00000000-0000-0000-0000-000000000007", "ops-user", "operator", ["order-service"], True, 0, None)),
        FakeConnection(None),
    ]
    connect = MagicMock(side_effect=connections)
    monkeypatch.setattr(app_module.psycopg, "connect", connect)
    async def exchange(_settings, _metadata, code, verifier):
        fake.token_params = {"code": code, "code_verifier": verifier}
        return {"id_token": "signed-id-token"}
    monkeypatch.setattr(app_module, "_exchange_oidc_code", exchange)
    monkeypatch.setattr(app_module, "start_outbox_dispatcher", lambda *_args: type("NoopDispatcher", (), {"stop": lambda self: None})())

    with TestClient(app_module.app) as client:
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 302
        state = fake.authorization_params["state"]
        state_cookie_name = app_module._oidc_state_cookie_name(state)
        assert f"{state_cookie_name}=" in login.headers["set-cookie"]
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=lax" in login.headers["set-cookie"].lower()
        assert fake.authorization_params["code_challenge_method"] == "S256"
        assert fake.authorization_params["scope"] == "openid profile email"
        assert client.cookies.get(state_cookie_name) == state

        callback = client.get(
            "/auth/oidc/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )
        assert fake.token_params is not None
        assert callback.status_code == 303
        assert callback.headers["location"] == "/"
        assert "aiops_session=" in callback.headers["set-cookie"]
        assert "httponly" in callback.headers["set-cookie"].lower()
        assert "max-age=900" in callback.headers["set-cookie"].lower()
        assert fake.token_params["code_verifier"] == "code-verifier"
        assert fake.token_params["code"] == "authorization-code"
        logout = client.post("/auth/logout", headers={"Origin": "https://aiops.example.com"})
        assert logout.status_code == 200
        statements = [statement for statement, _params in connections[3].cursor_instance.statements]
        assert any("DELETE FROM revoked_sessions" in statement for statement in statements)
        assert any("INSERT INTO revoked_sessions" in statement for statement in statements)
        assert len(connect.call_args_list) == 4


def test_parallel_oidc_logins_keep_independent_state_cookies(monkeypatch):
    configure_oidc(monkeypatch)
    fake = FakeOIDCClient("https://id.example.com/tenant")
    monkeypatch.setattr(app_module, "_oidc_client", lambda _settings: fake)
    connections = [
        FakeConnection((0, 1)),
        FakeConnection((0, 1)),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection(("00000000-0000-0000-0000-000000000007", "ops-user", "operator", ["order-service"], True, 0, None)),
    ]
    monkeypatch.setattr(app_module.psycopg, "connect", MagicMock(side_effect=connections))
    async def exchange(_settings, _metadata, _code, _verifier):
        return {"id_token": "signed-id-token"}
    monkeypatch.setattr(app_module, "_exchange_oidc_code", exchange)
    monkeypatch.setattr(app_module, "start_outbox_dispatcher", lambda *_args: type("NoopDispatcher", (), {"stop": lambda self: None})())

    with TestClient(app_module.app) as client:
        first_login = client.get("/auth/login", follow_redirects=False)
        first_state = parse_qs(urlsplit(first_login.headers["location"]).query)["state"][0]
        second_login = client.get("/auth/login", follow_redirects=False)
        second_state = parse_qs(urlsplit(second_login.headers["location"]).query)["state"][0]
        first_cookie = app_module._oidc_state_cookie_name(first_state)
        second_cookie = app_module._oidc_state_cookie_name(second_state)

        assert first_state != second_state
        assert first_cookie != second_cookie
        assert client.cookies.get(first_cookie) == first_state
        assert client.cookies.get(second_cookie) == second_state

        callback = client.get(
            "/auth/oidc/callback",
            params={"code": "first-authorization-code", "state": first_state},
            follow_redirects=False,
        )
        assert callback.status_code == 303


def test_oidc_callback_rejects_oversized_state_before_cookie_lookup(monkeypatch):
    configure_oidc(monkeypatch)
    monkeypatch.setattr(
        app_module,
        "_oidc_client",
        lambda _settings: FakeOIDCClient("https://id.example.com/tenant"),
    )
    with TestClient(app_module.app) as client:
        response = client.get(
            "/auth/oidc/callback",
            params={"code": "authorization-code", "state": "x" * 257},
        )
    assert response.status_code == 401


def test_inactive_oidc_user_requests_reactivation_once_without_receiving_a_session(monkeypatch):
    configure_oidc(monkeypatch)
    fake = FakeOIDCClient("https://id.example.com/tenant")
    monkeypatch.setattr(app_module, "_oidc_client", lambda _settings: fake)
    user_id = "00000000-0000-0000-0000-000000000007"
    requested_at = datetime(2026, 9, 27, tzinfo=timezone.utc)
    disabled = (user_id, "ops-user", "operator", ["order-service"], False, 1, None)
    pending = (user_id, "ops-user", "operator", ["order-service"], False, 1, requested_at)
    connections = [
        FakeConnection((0, 1)),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection([disabled, (requested_at,), pending]),
        FakeConnection((0, 1)),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection([pending]),
    ]
    connect = MagicMock(side_effect=connections)
    monkeypatch.setattr(app_module.psycopg, "connect", connect)

    async def exchange(_settings, _metadata, code, verifier):
        fake.token_params = {"code": code, "code_verifier": verifier}
        return {"id_token": "signed-id-token"}

    monkeypatch.setattr(app_module, "_exchange_oidc_code", exchange)
    monkeypatch.setattr(app_module, "start_outbox_dispatcher", lambda *_args: type("NoopDispatcher", (), {"stop": lambda self: None})())

    with TestClient(app_module.app) as client:
        for _ in range(2):
            fake.authorization_params = None
            client.get("/auth/login", follow_redirects=False)
            callback = client.get(
                "/auth/oidc/callback",
                params={"code": "authorization-code", "state": fake.authorization_params["state"]},
                follow_redirects=False,
            )
            assert callback.status_code == 403
            assert "aiops_session=" not in callback.headers.get("set-cookie", "")

    callback_sql = [statement for connection in (connections[2], connections[5]) for statement, _ in connection.cursor_instance.statements]
    assert sum("UPDATE users SET reactivation_requested_at" in statement for statement in callback_sql) == 1
    assert sum("user.reactivation_requested" in statement for statement in callback_sql) == 1
    assert connect.call_count == 6


def test_oidc_cookie_mutation_requires_exact_public_origin(monkeypatch):
    configure_oidc(monkeypatch)
    monkeypatch.setenv("AIOPS_AUTH_MODE", "local")
    monkeypatch.setenv("AIOPS_PUBLIC_ORIGIN", "https://aiops.example.com")
    with TestClient(app_module.app) as client:
        client.cookies.set(app_module.SESSION_COOKIE_NAME, "opaque-session")
        denied = client.post("/auth/logout")
        assert denied.status_code == 403
        accepted = client.post("/auth/logout", headers={"Origin": "https://aiops.example.com"})
        assert accepted.status_code == 200
        assert "aiops_session=" in accepted.headers["set-cookie"] and "Max-Age=0" in accepted.headers["set-cookie"]


def test_oidc_callback_verifies_signed_id_token_against_discovered_jwks(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def encode_integer(value):
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def encode_part(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()

    class Provider(BaseHTTPRequestHandler):
        issuer = ""
        code_challenge = ""
        code_verifier = ""
        nonce = ""

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/issuer/.well-known/openid-configuration":
                body = {
                    "issuer": self.issuer,
                    "authorization_endpoint": self.issuer + "/authorize",
                    "token_endpoint": self.issuer + "/token",
                    "jwks_uri": self.issuer + "/jwks",
                    "code_challenge_methods_supported": ["S256"],
                }
            elif self.path == "/issuer/jwks":
                body = {
                    "keys": [{
                        "kty": "RSA",
                        "use": "sig",
                        "alg": "RS256",
                        "kid": "test-key",
                        "n": encode_integer(public_numbers.n),
                        "e": encode_integer(public_numbers.e),
                    }]
                }
            else:
                self.send_error(404)
                return
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            form = parse_qs(self.rfile.read(length).decode())
            assert form["code"] == ["authorization-code"]
            assert form["code_verifier"] == [self.code_verifier]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
            assert challenge == self.code_challenge
            now = int(time.time())
            header = encode_part({"alg": "RS256", "kid": "test-key", "typ": "JWT"})
            claims = encode_part({
                "iss": self.issuer,
                "sub": "signed-subject",
                "aud": "aiops-workbench",
                "exp": now + 300,
                "iat": now,
                "nonce": self.nonce,
                "preferred_username": "signed-user",
                "groups": ["aiops-operators"],
            })
            signing_input = f"{header}.{claims}".encode()
            signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
            id_token = f"{header}.{claims}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
            payload = json.dumps({"access_token": "test-access-token", "token_type": "Bearer", "id_token": id_token}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Provider)
    Provider.issuer = f"http://127.0.0.1:{server.server_port}/issuer"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    configure_oidc(monkeypatch)
    monkeypatch.setenv("OIDC_ISSUER", Provider.issuer)
    monkeypatch.setenv("OIDC_METADATA_URL", Provider.issuer + "/.well-known/openid-configuration")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8081/auth/oidc/callback")
    monkeypatch.setenv("AIOPS_PUBLIC_ORIGIN", "http://localhost:8081")
    connections = [
        FakeConnection((0, 1)),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection(("00000000-0000-0000-0000-000000000007", "signed-user", "operator", ["order-service"], True, 0, None)),
    ]
    monkeypatch.setattr(app_module.psycopg, "connect", MagicMock(side_effect=connections))
    monkeypatch.setattr(app_module, "start_outbox_dispatcher", lambda *_args: type("NoopDispatcher", (), {"stop": lambda self: None})())

    try:
        with TestClient(app_module.app) as client:
            login = client.get("/auth/login", follow_redirects=False)
            assert login.status_code == 302
            state = client.cookies.get(app_module._oidc_state_cookie_name(fake.authorization_params["state"]))
            assert state
            authorization_params = parse_qs(urlsplit(login.headers["location"]).query)
            transaction_params = connections[0].cursor_instance.statements[-1][1]
            assert authorization_params["code_challenge_method"] == ["S256"]
            assert authorization_params["code_challenge"][0] == base64.urlsafe_b64encode(
                hashlib.sha256(transaction_params[1].encode()).digest()
            ).rstrip(b"=").decode()
            connections[1].cursor_instance.result = (transaction_params[1], transaction_params[2])
            Provider.code_challenge = authorization_params["code_challenge"][0]
            Provider.code_verifier = transaction_params[1]
            Provider.nonce = transaction_params[2]
            callback = client.get(
                "/auth/oidc/callback",
                params={"code": "authorization-code", "state": state},
                follow_redirects=False,
            )
            assert callback.status_code == 303
            assert "aiops_session=" in callback.headers["set-cookie"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_oidc_claims_sync_waits_for_disable_and_does_not_reactivate_user(monkeypatch):
    database_url = os.getenv("AIOPS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AIOPS_TEST_DATABASE_URL is not set")
    parsed_database_url = urlparse(database_url)
    if parsed_database_url.hostname not in {"postgres", "host.docker.internal", "127.0.0.1", "localhost"}:
        pytest.fail("integration tests only permit local Docker PostgreSQL hosts")
    if parsed_database_url.path != "/aiops_test":
        pytest.fail("OIDC lifecycle concurrency tests only permit the isolated aiops_test database")

    configure_oidc(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SESSION_SIGNING_KEY", "local-test-session-signing-key-123456")
    apply_migrations(database_url)

    user_id = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    subject = f"subject-{user_id}"
    issuer = "https://id.example.com/tenant"
    callback_state = f"lifecycle-state-{uuid.uuid4()}"

    class ClaimsClient(FakeOIDCClient):
        async def parse_id_token(self, token, *, nonce):
            assert token["id_token"] == "signed-id-token"
            assert nonce == "saved-nonce"
            return {
                "iss": self.issuer,
                "sub": subject,
                "preferred_username": "synced-operator",
                "groups": ["aiops-operators"],
            }

    target_username = f"lifecycle-target-{user_id[:8]}"
    admin_username = f"lifecycle-admin-{admin_id[:8]}"
    client = ClaimsClient(issuer)
    monkeypatch.setattr(app_module, "_oidc_client", lambda _settings: client)

    async def exchange(_settings, _metadata, _code, _verifier):
        return {"id_token": "signed-id-token"}

    monkeypatch.setattr(app_module, "_exchange_oidc_code", exchange)

    with psycopg.connect(database_url) as conn:
        conn.execute(
            """INSERT INTO users (id, username, role, resource_scopes, oidc_issuer, oidc_subject, active)
               VALUES (%s, %s, 'viewer', %s, %s, %s, TRUE)""",
            (user_id, target_username, ["legacy-service"], issuer, subject),
        )
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, resource_scopes) VALUES (%s, %s, %s, 'admin', %s)",
            (admin_id, admin_username, "not-used-by-signed-session", ["order-service"]),
        )
        conn.execute(
            """INSERT INTO oidc_login_transactions (state_hash, code_verifier, nonce, expires_at)
               VALUES (%s, %s, %s, now() + interval '5 minutes')""",
            (hashlib.sha256(callback_state.encode()).hexdigest(), "unused-test-verifier", "saved-nonce"),
        )

    admin_token = create_session(
        Principal(admin_id, admin_username, "admin", ("order-service",), 0),
        os.environ["SESSION_SIGNING_KEY"],
    )
    claims_sync_has_lock = threading.Event()
    disable_waiting = threading.Event()
    release_claims_sync = threading.Event()
    real_connect_database = app_module.connect_database

    class CursorProxy:
        def __init__(self, cursor, operation):
            self.cursor = cursor
            self.operation = operation

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, statement, params=None):
            is_lifecycle_lock = (
                statement.strip().upper() == "SELECT PG_ADVISORY_XACT_LOCK(%S)"
                and params == (app_module.USER_LIFECYCLE_LOCK,)
            )
            if is_lifecycle_lock and self.operation == "disable":
                disable_waiting.set()
            result = self.cursor.execute(statement, params)
            if is_lifecycle_lock and self.operation == "claims_sync":
                claims_sync_has_lock.set()
                if not release_claims_sync.wait(timeout=10):
                    raise TimeoutError("test did not release the lifecycle lock holder")
            return result

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class ConnectionProxy:
        def __init__(self, connection, operation):
            self.connection = connection
            self.operation = operation

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def cursor(self, *args, **kwargs):
            return CursorProxy(self.connection.cursor(*args, **kwargs), self.operation)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    operation_context = threading.local()

    def instrumented_connect_database(*args, **kwargs):
        connection = real_connect_database(*args, **kwargs)
        return ConnectionProxy(connection, getattr(operation_context, "name", "other"))

    monkeypatch.setattr(app_module, "connect_database", instrumented_connect_database)

    def request_for(path, query="", cookie_name=app_module.SESSION_COOKIE_NAME, token=admin_token):
        return Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET" if path == "/auth/oidc/callback" else "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [(b"cookie", f"{cookie_name}={token}".encode())],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        })

    def request_disable():
        operation_context.name = "disable"
        return app_module.disable_user(
            user_id,
            request_for(f"/api/users/{user_id}/disable"),
            authorization=None,
        )

    def request_callback():
        operation_context.name = "claims_sync"
        request = request_for(
            "/auth/oidc/callback",
            urlencode({"code": "authorization-code", "state": callback_state}),
            cookie_name=app_module._oidc_state_cookie_name(callback_state),
            token=callback_state,
        )
        return asyncio.run(app_module.oidc_callback(request))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            callback_future = executor.submit(request_callback)
            assert claims_sync_has_lock.wait(timeout=5), "claims sync did not acquire the lifecycle lock"
            disable_future = executor.submit(request_disable)
            assert disable_waiting.wait(timeout=5), "disable did not reach the lifecycle lock"
            assert not disable_future.done(), "disable bypassed the lifecycle lock"
            release_claims_sync.set()
            callback_response = callback_future.result(timeout=10)
            disable_result = disable_future.result(timeout=10)

        assert callback_response.status_code == 303
        assert disable_result["status"] == "disabled"

        with psycopg.connect(database_url) as conn:
            user = conn.execute(
                "SELECT username, role, resource_scopes, active, session_generation FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
            assert user == ("synced-operator", "operator", ["order-service"], False, 1)
    finally:
        release_claims_sync.set()
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM oidc_login_transactions WHERE state_hash = %s",
                    (hashlib.sha256(callback_state.encode()).hexdigest(),),
                )
                cursor.execute("DELETE FROM audit_events WHERE actor_id = ANY(%s::uuid[])", ([user_id, admin_id],))
                cursor.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", ([user_id, admin_id],))
