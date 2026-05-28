import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.deps import get_chip_service
from core.domain.models import ChipSummary


@pytest.fixture
def client():
    return TestClient(app)


def _row():
    return ChipSummary(
        symbol="600519.SH",
        trade_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        profit_ratio=0.7, avg_cost=1500.0,
        cost_90_low=1400.0, cost_90_high=1600.0, concentration_90=0.1,
        cost_70_low=1450.0, cost_70_high=1550.0, concentration_70=0.05,
    )


async def test_chip_summary_returns_db_rows(client):
    svc = MagicMock()
    svc.get_summary_cache_only = AsyncMock(return_value=[_row()])
    app.dependency_overrides[get_chip_service] = lambda: svc
    try:
        r = client.get("/api/symbols/600519.SH/chip_summary?days=90")
        assert r.status_code == 200
        data = r.json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["profit_ratio"] == 0.7
        assert data["meta"]["stale"] is False
    finally:
        app.dependency_overrides.clear()


async def test_chip_summary_stale_when_no_data(client):
    svc = MagicMock()
    svc.get_summary_cache_only = AsyncMock(return_value=[])
    app.dependency_overrides[get_chip_service] = lambda: svc
    try:
        r = client.get("/api/symbols/600519.SH/chip_summary?days=90")
        assert r.status_code == 200
        data = r.json()
        assert data["rows"] == []
        assert data["meta"]["stale"] is True
        assert data["meta"]["reason"] == "warming_up"
    finally:
        app.dependency_overrides.clear()


async def test_chip_summary_non_ashare_stale(client):
    svc = MagicMock()
    svc.get_summary_cache_only = AsyncMock(return_value=[])
    app.dependency_overrides[get_chip_service] = lambda: svc
    try:
        r = client.get("/api/symbols/00700.HK/chip_summary?days=90")
        assert r.status_code == 200
        assert r.json()["meta"]["reason"] == "non_ashare"
        # cache_only 不应被调用(非 A 股直接返回)
        svc.get_summary_cache_only.assert_not_called()
    finally:
        app.dependency_overrides.clear()
