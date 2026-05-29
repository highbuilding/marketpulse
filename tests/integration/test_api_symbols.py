from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import (
    get_chip_service, get_fund_flow_service, get_kline_service,
)
from apps.api.main import app
from core.domain.models import Bar, ChipSummary


def _bar(symbol, day, close=100.0, interval="1d"):
    return Bar(market="ashare", symbol=symbol,
               ts=datetime(2026, 5, day, tzinfo=timezone.utc),
               open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
               close=Decimal(str(close)), volume=1_000_000, interval=interval,
               amount=20_000_000, turnover=2.1)


def test_bars_returns_400_for_bad_interval():
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/bars?interval=2d")
    assert resp.status_code == 400


def test_bars_returns_ok(monkeypatch):
    import fakeredis.aioredis
    from apps.api.deps import get_redis_cache
    from core.cache.redis_client import RedisCache

    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    svc = get_kline_service()
    monkeypatch.setattr(svc, "get_bars_cache_only",
                        AsyncMock(return_value=([_bar("600519.SH", 13, 1344.09)], False)))
    app.dependency_overrides[get_redis_cache] = lambda: cache
    try:
        with TestClient(app) as client:
            resp = client.get("/api/symbols/600519.SH/bars?interval=1d&days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "600519.SH"
        assert len(body["bars"]) == 1
        assert body["bars"][0]["close"] == pytest.approx(1344.09)
        assert body["bars"][0]["amount"] == pytest.approx(20_000_000)
    finally:
        app.dependency_overrides.clear()


def test_fund_flow_returns_ok(monkeypatch):
    svc = get_fund_flow_service()
    monkeypatch.setattr(svc, "query_symbol", AsyncMock(return_value=[]))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/fund_flow?days=10")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def test_chip_summary_returns_ok(monkeypatch):
    svc = get_chip_service()
    monkeypatch.setattr(svc, "get_summary_cache_only", AsyncMock(return_value=[
        ChipSummary(
            symbol="002415.SZ",
            trade_date=datetime(2026, 5, 20, tzinfo=timezone.utc),
            profit_ratio=0.6,
            avg_cost=38.5,
            cost_90_low=30,
            cost_90_high=45,
            concentration_90=0.2,
            cost_70_low=34,
            cost_70_high=41,
            concentration_70=0.12,
        )
    ]))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/002415.SZ/chip_summary?days=90")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"][0]["avg_cost"] == pytest.approx(38.5)


def test_chip_summary_non_ashare_returns_empty():
    with TestClient(app) as client:
        resp = client.get("/api/symbols/AAPL/chip_summary?days=90")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def test_volume_indicators_returns_ok(monkeypatch):
    kline = get_kline_service()
    monkeypatch.setattr(kline, "get_bars_cache_only", AsyncMock(return_value=(
        [_bar("002415.SZ", i + 1, close=10 + i) for i in range(21)], False,
    )))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/002415.SZ/volume_indicators?interval=1d&days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["interval"] == "1d"
    assert body["rows"][-1]["vol_ma20"] is not None
    assert "single_bar_volume_ratio" in body["rows"][-1]


def test_volume_indicators_accepts_5m(monkeypatch):
    kline = get_kline_service()
    monkeypatch.setattr(kline, "get_bars_cache_only", AsyncMock(return_value=(
        [_bar("002415.SZ", i + 1, interval="5m", close=10 + i) for i in range(21)], False,
    )))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/002415.SZ/volume_indicators?interval=5m&days=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["interval"] == "5m"
    assert body["rows"][-1]["volume_ratio"] is not None
    assert "single_bar_volume_ratio" in body["rows"][-1]
