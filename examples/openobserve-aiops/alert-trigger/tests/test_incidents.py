from fastapi.testclient import TestClient

from app import app


def test_health_endpoint_is_available():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
