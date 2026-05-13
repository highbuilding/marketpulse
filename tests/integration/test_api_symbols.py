from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_fund_flow_service, get_kline_service
from apps.api.main import app
from core.domain.models import Bar


def _bar(symbol, day, close=100.0):
    return Bar(market="ashare", symbol=symbol,
               ts=datetime(2026, 5, day, tzinfo=timezone.utc),
               open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
               close=Decimal(str(close)), volume=1_000_000, interval="1d")


def test_bars_returns_400_for_bad_interval():
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/bars?interval=2d")
    assert resp.status_code == 400


def test_bars_returns_ok(monkeypatch):
    svc = get_kline_service()
    monkeypatch.setattr(svc, "get_bars",
                        AsyncMock(return_value=[_bar("600519.SH", 13, 1344.09)]))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/bars?interval=1d&days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "600519.SH"
    assert len(body["bars"]) == 1
    assert body["bars"][0]["close"] == pytest.approx(1344.09)


def test_fund_flow_returns_ok(monkeypatch):
    svc = get_fund_flow_service()
    monkeypatch.setattr(svc, "query_symbol", AsyncMock(return_value=[]))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/fund_flow?days=10")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []
