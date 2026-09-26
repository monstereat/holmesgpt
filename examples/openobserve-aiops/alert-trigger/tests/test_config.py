from unittest.mock import MagicMock

import psycopg
import pytest
from redis.connection import SSLConnection, parse_url
from fastapi import HTTPException
from fastapi.testclient import TestClient

from broker_config import validate_broker_url
from db_config import validate_database_url
from app import (
    API_METRICS_LOCK,
    API_REQUEST_METRICS,
    _auth_mode,
    _database_url as api_database_url,
    _require_demo_actions_enabled,
    _max_pending_tasks,
    _oldest_pending_task_age_slo_seconds,
    _test_users_configuration,
    _validate_metrics_configuration,
    app,
    readyz,
)
from worker import (
    _database_url as worker_database_url,
    _holmes_client,
    validate_worker_configuration,
)


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


def test_local_login_defaults_prefill_operator_only_in_local_mode(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.setenv(
        "AIOPS_TEST_USERS_JSON",
        '[{"username":"operator","password":"test-password","role":"operator","resource_scopes":["order-service"]}]',
    )
    response = TestClient(app).get("/auth/local-test-defaults")
    assert response.status_code == 200
    assert response.json() == {"username": "operator", "password": "test-password"}
    assert response.headers["cache-control"] == "no-store"


def test_local_login_defaults_are_not_exposed_in_production(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.delenv("AIOPS_AUTH_MODE", raising=False)
    monkeypatch.delenv("AIOPS_TEST_USERS_JSON", raising=False)
    response = TestClient(app).get("/auth/local-test-defaults")
    assert response.status_code == 404


def test_demo_remediation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AIOPS_DEMO_ACTIONS_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc:
        _require_demo_actions_enabled()
    assert exc.value.status_code == 503


def test_metrics_token_is_required_outside_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("AIOPS_METRICS_TOKEN", "short")
    with pytest.raises(RuntimeError, match="AIOPS_METRICS_TOKEN"):
        _validate_metrics_configuration()
    monkeypatch.setenv("AIOPS_METRICS_TOKEN", "m" * 40)
    _validate_metrics_configuration()


def test_non_local_database_urls_require_full_tls_verification(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    with pytest.raises(RuntimeError, match="valid PostgreSQL connection string"):
        validate_database_url("not-a-postgresql-url", "DATABASE_URL", required=True)

    for database_url in (
        "postgresql://aiops:secret@db.internal/aiops",
        "postgresql://aiops:secret@db.internal/aiops?sslmode=require",
        "postgresql://aiops:secret@db.internal/aiops?sslmode=verify-ca",
    ):
        with pytest.raises(RuntimeError, match="sslmode=verify-full"):
            validate_database_url(database_url, "DATABASE_URL", required=True)

    database_url = "postgresql://aiops:secret@db.internal/aiops?sslmode=verify-full"
    assert validate_database_url(database_url, "DATABASE_URL", required=True) == database_url


def test_database_url_is_required_outside_local_and_optional_locally(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        validate_database_url("", "DATABASE_URL", required=True)

    monkeypatch.setenv("AIOPS_ENV", "local")
    assert validate_database_url("", "DATABASE_URL") == ""
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        validate_database_url("", "DATABASE_URL", required=True)
    database_url = "postgresql://aiops@postgres/aiops"
    assert validate_database_url(database_url, "DATABASE_URL") == database_url


def test_api_and_worker_reject_non_tls_database_urls(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://aiops:secret@db.internal/aiops")

    with pytest.raises(HTTPException, match="sslmode=verify-full"):
        api_database_url()
    with pytest.raises(RuntimeError, match="sslmode=verify-full"):
        worker_database_url()


def test_worker_configuration_fails_before_consuming_tasks_without_holmes(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime:secret@db.internal/aiops?sslmode=verify-full")
    monkeypatch.delenv("HOLMES_API_URL", raising=False)
    monkeypatch.delenv("HOLMES_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="HOLMES_API_URL and HOLMES_API_KEY"):
        validate_worker_configuration()


def test_worker_requires_https_holmes_endpoint_outside_local_mode(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("HOLMES_API_URL", "http://holmes.internal:5050")
    monkeypatch.setenv("HOLMES_API_KEY", "worker-key")
    with pytest.raises(RuntimeError, match="HOLMES_API_URL must use HTTPS"):
        _holmes_client()


def test_worker_accepts_valid_production_holmes_configuration(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime:secret@db.internal/aiops?sslmode=verify-full")
    monkeypatch.setenv("HOLMES_API_URL", "https://holmes.internal")
    monkeypatch.setenv("HOLMES_API_KEY", "worker-key")
    monkeypatch.setenv("HOLMES_TIMEOUT_SECONDS", "120")
    validate_worker_configuration()


def test_non_local_broker_url_requires_tls_auth_and_hostname_verification(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    for broker_url in (
        "redis://:secret@broker.internal:6379/0",
        "rediss://broker.internal:6379/0?ssl_cert_reqs=required&ssl_check_hostname=true",
        "rediss://:secret@broker.internal:invalid/0?ssl_cert_reqs=required&ssl_check_hostname=true",
        "rediss://:secret@broker.internal:6379/0?ssl_cert_reqs=required&ssl_check_hostname=false",
        "rediss://:secret@broker.internal:6379/0?ssl_cert_reqs=required",
        "rediss://:secret@broker.internal:6379/0?ssl_cert_reqs=none&ssl_check_hostname=true",
    ):
        with pytest.raises(RuntimeError):
            validate_broker_url(broker_url, required=True)

    broker_url = "rediss://:secret@broker.internal:6379/0?ssl_cert_reqs=required&ssl_check_hostname=true"
    assert validate_broker_url(broker_url, required=True) == broker_url
    options = parse_url(broker_url)
    assert options["connection_class"] is SSLConnection
    assert options["ssl_cert_reqs"] == "required"
    assert options["ssl_check_hostname"] is True


def test_local_broker_url_can_use_the_isolated_compose_network(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    broker_url = "redis://redis:6379/0"
    assert validate_broker_url(broker_url, required=True) == broker_url


def test_pending_task_capacity_is_required_and_validated_outside_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.delenv("AIOPS_MAX_PENDING_TASKS", raising=False)
    with pytest.raises(RuntimeError, match="AIOPS_MAX_PENDING_TASKS"):
        _max_pending_tasks()
    monkeypatch.setenv("AIOPS_MAX_PENDING_TASKS", "0")
    with pytest.raises(RuntimeError, match="positive integer"):
        _max_pending_tasks()
    monkeypatch.setenv("AIOPS_MAX_PENDING_TASKS", "250")
    assert _max_pending_tasks() == 250


def test_pending_task_capacity_is_optional_only_for_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.delenv("AIOPS_MAX_PENDING_TASKS", raising=False)
    assert _max_pending_tasks() is None


def test_oldest_pending_task_age_slo_is_required_and_validated_outside_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "production")
    monkeypatch.delenv("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS", raising=False)
    with pytest.raises(RuntimeError, match="AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS"):
        _oldest_pending_task_age_slo_seconds()
    for value in ("0", "-1", "nan", "inf", "not-a-number"):
        monkeypatch.setenv("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS", value)
        with pytest.raises(RuntimeError, match="positive"):
            _oldest_pending_task_age_slo_seconds()
    monkeypatch.setenv("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS", "90")
    assert _oldest_pending_task_age_slo_seconds() == 90


def test_oldest_pending_task_age_slo_is_optional_only_in_local_runtime(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.delenv("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS", raising=False)
    assert _oldest_pending_task_age_slo_seconds() is None


def test_metrics_endpoint_is_disabled_without_a_token(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.delenv("AIOPS_METRICS_TOKEN", raising=False)
    with TestClient(app) as client:
        assert client.get("/_internal/metrics").status_code == 404


def test_metrics_endpoint_requires_token_and_returns_queue_metrics(monkeypatch):
    monkeypatch.setenv("AIOPS_ENV", "local")
    monkeypatch.setenv("AIOPS_METRICS_TOKEN", "m" * 40)
    monkeypatch.setenv("AIOPS_MAX_PENDING_TASKS", "250")
    monkeypatch.setenv("AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS", "90")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    cursor = MagicMock()
    cursor.fetchall.return_value = [("queued", 2), ("completed", 4)]
    cursor.fetchone.side_effect = [(5, 1.5, 9.2), (7.5,), (3,)]
    cursor.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.__enter__.return_value = connection
    monkeypatch.setattr("app.psycopg.connect", lambda *args, **kwargs: connection)
    with API_METRICS_LOCK:
        API_REQUEST_METRICS.clear()

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        incident_id = "00000000-0000-0000-0000-000000000001"
        assert client.get(f"/api/incidents/{incident_id}").status_code == 401
        assert client.get("/_internal/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/_internal/metrics", headers={"Authorization": f"Bearer {'m' * 40}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'aiops_tasks{status="queued"} 2' in response.text
    assert 'aiops_tasks{status="failed"} 0' in response.text
    assert "aiops_oldest_pending_task_age_seconds 7.5" in response.text
    assert "aiops_task_retry_attempts_total 3" in response.text
    assert "aiops_pending_task_capacity 250" in response.text
    assert "aiops_oldest_pending_task_age_slo_seconds 90.0" in response.text
    assert 'aiops_worker_task_duration_seconds_windowed{quantile="0.50"} 1.5' in response.text
    assert 'aiops_worker_task_duration_seconds_windowed{quantile="0.95"} 9.2' in response.text
    assert "aiops_worker_task_duration_samples_windowed 5" in response.text
    assert "aiops_api_requests_in_flight 0" in response.text
    assert 'aiops_api_requests_total{method="GET",route="/healthz",status_class="2xx"} 1' in response.text
    assert 'route="/api/incidents/{incident_id}"' in response.text
    assert incident_id not in response.text
    assert "aiops_api_request_duration_seconds_bucket" in response.text


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
