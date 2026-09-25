import pytest
from fastapi import HTTPException

from app import _require_demo_actions_enabled, _test_users_configuration


def test_test_user_seeding_requires_local_environment(monkeypatch):
    monkeypatch.setenv("AIOPS_TEST_USERS_JSON", "[]")
    monkeypatch.delenv("AIOPS_ENV", raising=False)
    with pytest.raises(RuntimeError, match="AIOPS_ENV=local"):
        _test_users_configuration()


def test_test_user_seeding_is_available_in_local_environment(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.setenv("AIOPS_TEST_USERS_JSON", "[]")
    assert _test_users_configuration() == "[]"


def test_test_user_seeding_is_disabled_without_test_user_configuration(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.delenv("AIOPS_TEST_USERS_JSON", raising=False)
    assert _test_users_configuration() == ""


def test_demo_remediation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AIOPS_DEMO_ACTIONS_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc:
        _require_demo_actions_enabled()
    assert exc.value.status_code == 503
