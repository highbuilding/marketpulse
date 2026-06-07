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


def test_positions_route_crud_and_soft_delete(client):
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

    listed = client.get("/api/positions?market=ashare")
    assert listed.status_code == 200
    assert listed.json()["positions"][0]["symbol"] == "002415.SZ"

    patched = client.patch(
        "/api/positions/002415.SZ?market=ashare",
        json={"quantity": 200, "note": "观察承接"},
    )
    assert patched.status_code == 200
    assert client.get("/api/positions?market=ashare").json()["positions"][0]["quantity"] == 200

    deleted = client.delete("/api/positions/002415.SZ?market=ashare")
    assert deleted.status_code == 204
    assert client.get("/api/positions?market=ashare").json()["positions"] == []
    closed = client.get("/api/positions?market=ashare&include_closed=true").json()["positions"]
    assert closed[0]["status"] == "closed"


def test_positions_route_rejects_non_ashare(client):
    response = client.post(
        "/api/positions",
        json={"market": "us", "symbol": "AAPL", "quantity": 1},
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"]
