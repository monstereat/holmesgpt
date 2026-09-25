from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import _auth_mode, _require_demo_actions_enabled, _test_users_configuration, app, readyz


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


def test_local_password_auth_is_rejected_outside_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("AIOPS_AUTH_MODE", "local")
    with pytest.raises(RuntimeError, match="only permitted when AIOPS_ENV=local"):
        _auth_mode()


def test_oidc_is_the_default_auth_mode_outside_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.delenv("AIOPS_AUTH_MODE", raising=False)
    assert _auth_mode() == "oidc"


def test_demo_remediation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AIOPS_DEMO_ACTIONS_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc:
        _require_demo_actions_enabled()
    assert exc.value.status_code == 503


def test_incident_api_readiness_fails_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503


def test_incident_api_readiness_reports_database_connectivity(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    connection = MagicMock()
    monkeypatch.setattr("app.psycopg.connect", lambda *args, **kwargs: connection)
    assert readyz() == {"status": "ready"}

    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("database offline")

    monkeypatch.setattr("app.psycopg.connect", unavailable)
    with pytest.raises(HTTPException) as exc:
        readyz()
    assert exc.value.status_code == 503
    assert exc.value.detail == "Incident database is unavailable"
