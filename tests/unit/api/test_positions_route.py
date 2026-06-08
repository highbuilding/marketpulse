import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.api.deps import get_position_service
from apps.api.routes.positions import router
from core.persistence.position_repo import PositionRepo
from core.persistence.sqlite_repo import StateRepo
from core.positions.service import PositionService


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "state.db")
    state = StateRepo(db_path)
    asyncio.run(state.init())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_position_service] = (
        lambda: PositionService(PositionRepo(db_path))
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_positions_route_crud(client):
    # 创建
    created = client.post(
        "/api/positions",
        json={
            "market": "ashare",
            "symbol": "002415.sz",
            "name": "海康威视",
            "quantity": 100,
            "cost_price": 31.2,
            "entry_reason": "低位启动",
        },
    )
    assert created.status_code == 200
    pid = created.json()["id"]
    assert pid > 0

    listed = client.get("/api/positions?market=ashare")
    assert listed.status_code == 200
    assert listed.json()["positions"][0]["symbol"] == "002415.SZ"

    # 按 id 编辑
    patched = client.patch(
        f"/api/positions/{pid}",
        json={"quantity": 200, "note": "观察承接"},
    )
    assert patched.status_code == 200
    assert client.get("/api/positions?market=ashare").json()["positions"][0]["quantity"] == 200

    # 按 id 平仓: 手填平仓价 → 盈亏存库 ((33-31.2)*200 = 360)
    closed_resp = client.post(f"/api/positions/{pid}/close", json={"close_price": 33.0})
    assert closed_resp.status_code == 200
    assert client.get("/api/positions?market=ashare").json()["positions"] == []
    closed = client.get("/api/positions?market=ashare&include_closed=true").json()["positions"]
    assert closed[0]["status"] == "closed"
    assert closed[0]["close_price"] == 33.0
    assert abs(closed[0]["profit_amount"] - 360.0) < 1e-6


def test_positions_route_allows_multiple_same_symbol(client):
    # A 方案: 同标的可多条
    id1 = client.post("/api/positions", json={"market": "ashare", "symbol": "002415.SZ", "quantity": 100}).json()["id"]
    id2 = client.post("/api/positions", json={"market": "ashare", "symbol": "002415.SZ", "quantity": 200}).json()["id"]
    assert id1 != id2
    assert len(client.get("/api/positions?market=ashare").json()["positions"]) == 2


def test_positions_route_delete_by_id(client):
    pid = client.post("/api/positions", json={"market": "ashare", "symbol": "002415.SZ"}).json()["id"]
    assert client.delete(f"/api/positions/{pid}").status_code == 204
    assert client.get("/api/positions?market=ashare&include_closed=true").json()["positions"] == []


def test_positions_route_rejects_non_ashare(client):
    response = client.post(
        "/api/positions",
        json={"market": "us", "symbol": "AAPL", "quantity": 1},
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"]
