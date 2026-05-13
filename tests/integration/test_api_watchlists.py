import uuid

from fastapi.testclient import TestClient

from apps.api.main import app


def test_create_list_add_symbol_flow():
    name = f"测试-{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        resp = client.post("/api/watchlists", json={"name": name})
        assert resp.status_code == 200
        wl_id = resp.json()["id"]

        resp = client.get("/api/watchlists")
        names = [w["name"] for w in resp.json()["watchlists"]]
        assert name in names

        resp = client.post(f"/api/watchlists/{wl_id}/symbols",
                            json={"symbol": "600519.SH"})
        assert resp.status_code == 204

        resp = client.get(f"/api/watchlists/{wl_id}/symbols")
        assert resp.json()["symbols"] == ["600519.SH"]

        resp = client.delete(f"/api/watchlists/{wl_id}/symbols/600519.SH")
        assert resp.status_code == 204
        resp = client.get(f"/api/watchlists/{wl_id}/symbols")
        assert resp.json()["symbols"] == []

        resp = client.delete(f"/api/watchlists/{wl_id}")
        assert resp.status_code == 204
        names = [w["name"] for w in client.get("/api/watchlists").json()["watchlists"]]
        assert name not in names


def test_create_rejects_empty_name():
    with TestClient(app) as client:
        resp = client.post("/api/watchlists", json={"name": "   "})
    assert resp.status_code == 400
