"""Local-demo password hashing, signed sessions, and resource authorization."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Iterable

from models import Principal

PASSWORD_ROUNDS = 310_000
SESSION_TTL_SECONDS = 8 * 60 * 60
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"incident:read", "task:read"}),
    "operator": frozenset({"incident:read", "task:read", "task:create", "task:retry"}),
    "approver": frozenset({"incident:read", "task:read", "approval:review", "incident:review"}),
    "admin": frozenset({"incident:read", "task:read", "task:create", "task:retry", "approval:review", "incident:review", "user:manage"}),
}


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ROUNDS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        salt = _decode(salt_text)
        expected = _decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _signing_key(key: str | bytes) -> bytes:
    raw = key.encode() if isinstance(key, str) else key
    if len(raw) < 32:
        raise ValueError("Session signing key must contain at least 32 bytes")
    return raw


def create_session(
    principal: Principal,
    key: str | bytes,
    *,
    now: int | None = None,
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> str:
    if isinstance(ttl_seconds, bool) or not 60 <= ttl_seconds <= SESSION_TTL_SECONDS:
        raise ValueError("Session lifetime must be between 60 seconds and 8 hours")
    issued = int(time.time() if now is None else now)
    payload = {
        "sub": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "scopes": list(principal.resource_scopes),
        "iat": issued,
        "exp": issued + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    body = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(_signing_key(key), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_encode(signature)}"


def parse_session(token: str, key: str | bytes, *, now: int | None = None) -> Principal:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_signing_key(key), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            raise ValueError("Invalid session")
        payload = json.loads(_decode(body))
        current = int(time.time() if now is None else now)
        if not isinstance(payload, dict) or int(payload["exp"]) <= current:
            raise ValueError("Expired session")
        role = payload["role"]
        scopes = payload["scopes"]
        if role not in ROLE_PERMISSIONS or not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
            raise ValueError("Invalid session claims")
        return Principal(
            user_id=str(payload["sub"]),
            username=str(payload["username"]),
            role=role,
            resource_scopes=tuple(scopes),
        )
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid session") from exc


def session_expiration(token: str, key: str | bytes, *, now: int | None = None) -> int:
    parse_session(token, key, now=now)
    body, _signature = token.split(".", 1)
    payload = json.loads(_decode(body))
    return int(payload["exp"])


def authorize(principal: Principal, permission: str, resource: str) -> bool:
    return (
        permission in ROLE_PERMISSIONS.get(principal.role, frozenset())
        and ("*" in principal.resource_scopes or resource in principal.resource_scopes)
    )


def require_permission(principal: Principal, permission: str, resource: str) -> None:
    if not authorize(principal, permission, resource):
        raise PermissionError("Not authorized for this resource")


def validate_test_identities(rows: Iterable[dict[str, str]]) -> list[Principal]:
    """Validate runtime-provided local test identities before DB seeding."""
    principals = []
    usernames: set[str] = set()
    for row in rows:
        username, role = row.get("username", ""), row.get("role", "")
        if not username or username in usernames or role not in ROLE_PERMISSIONS:
            raise ValueError("Invalid or duplicate test identity")
        usernames.add(username)
        principals.append(Principal(row.get("user_id", ""), username, role, tuple(row.get("resource_scopes", "").split(","))))
    return principals
