from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_status() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert payload["checks"]["neo4j"]["status"] in {"disabled", "ok", "degraded"}
