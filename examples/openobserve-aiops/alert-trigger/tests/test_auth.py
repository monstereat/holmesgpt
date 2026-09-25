import pytest

from auth import authorize, create_session, hash_password, parse_session, require_permission, verify_password
from models import Principal


def test_password_hash_and_verification():
    encoded = hash_password("demo-password-long", salt=b"0123456789abcdef")
    assert encoded != "demo-password-long"
    assert verify_password("demo-password-long", encoded)
    assert not verify_password("wrong-password", encoded)
    with pytest.raises(ValueError, match="12 characters"):
        hash_password("short")


def test_signed_session_rejects_tampering_and_expired_claims():
    principal = Principal("user-1", "operator", "operator", ("order-service",))
    token = create_session(principal, "x" * 32, now=100)
    assert parse_session(token, "x" * 32, now=101) == principal
    with pytest.raises(ValueError):
        parse_session(token + "x", "x" * 32, now=101)
    with pytest.raises(ValueError, match="Expired"):
        parse_session(token, "x" * 32, now=100 + 8 * 60 * 60)
    with pytest.raises(ValueError, match="32 bytes"):
        create_session(principal, "short")


def test_roles_and_resource_scopes_are_both_required():
    viewer = Principal("u1", "viewer", "viewer", ("order-service",))
    operator = Principal("u2", "operator", "operator", ("order-service",))
    approver = Principal("u3", "approver", "approver", ("order-service",))
    assert authorize(viewer, "incident:read", "order-service")
    assert not authorize(viewer, "task:create", "order-service")
    assert not authorize(operator, "task:create", "billing")
    assert authorize(approver, "approval:review", "order-service")
    with pytest.raises(PermissionError):
        require_permission(viewer, "approval:review", "order-service")
