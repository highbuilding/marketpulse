from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_endpoint_structure():
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "down"}
    assert "adapters" in body
    assert "markets_enabled" in body
