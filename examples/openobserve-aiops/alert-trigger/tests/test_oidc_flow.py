import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from fastapi.testclient import TestClient

import app as app_module


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
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchone(self):
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
        FakeConnection(None),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection(("00000000-0000-0000-0000-000000000007", "ops-user", "operator", ["order-service"], True)),
    ]
    connect = MagicMock(side_effect=connections)
    monkeypatch.setattr(app_module.psycopg, "connect", connect)
    async def exchange(_settings, _metadata, code, verifier):
        fake.token_params = {"code": code, "code_verifier": verifier}
        return {"id_token": "signed-id-token"}
    monkeypatch.setattr(app_module, "_exchange_oidc_code", exchange)
    monkeypatch.setattr(app_module, "start_outbox_dispatcher", lambda: type("NoopDispatcher", (), {"stop": lambda self: None})())

    with TestClient(app_module.app) as client:
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 302
        assert "aiops_oidc_state=" in login.headers["set-cookie"]
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=lax" in login.headers["set-cookie"].lower()
        assert fake.authorization_params["code_challenge_method"] == "S256"
        assert fake.authorization_params["scope"] == "openid profile email"
        state = fake.authorization_params["state"]
        assert client.cookies.get(app_module.OIDC_STATE_COOKIE_NAME) == state

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
        assert len(connect.call_args_list) == 3


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
        FakeConnection(None),
        FakeConnection(("code-verifier", "saved-nonce")),
        FakeConnection(("00000000-0000-0000-0000-000000000007", "signed-user", "operator", ["order-service"], True)),
    ]
    monkeypatch.setattr(app_module.psycopg, "connect", MagicMock(side_effect=connections))
    monkeypatch.setattr(app_module, "start_outbox_dispatcher", lambda: type("NoopDispatcher", (), {"stop": lambda self: None})())

    try:
        with TestClient(app_module.app) as client:
            login = client.get("/auth/login", follow_redirects=False)
            assert login.status_code == 302
            state = client.cookies.get(app_module.OIDC_STATE_COOKIE_NAME)
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
