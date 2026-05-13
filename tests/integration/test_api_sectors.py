from fastapi.testclient import TestClient

from apps.api.main import app


def test_list_sectors_empty_db_returns_empty():
    with TestClient(app) as client:
        resp = client.get("/api/sectors/list")
    assert resp.status_code == 200
    assert "sectors" in resp.json()


def test_constituents_404_for_unknown():
    with TestClient(app) as client:
        resp = client.get("/api/sectors/UNKNOWN/constituents")
    assert resp.status_code == 404
