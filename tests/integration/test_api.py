from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_quote_cache
from apps.api.main import app
from core.domain.models import Quote


def test_health_endpoint_structure():
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "down"}
    assert "adapters" in body
    assert "markets_enabled" in body


def test_overview_returns_404_for_unknown_market():
    with TestClient(app) as client:
        resp = client.get("/api/markets/unknown/overview")
    assert resp.status_code == 404


def test_overview_returns_warming_for_cold_cache():
    cache = get_quote_cache()
    cache._store.clear()
    with TestClient(app) as client:
        resp = client.get("/api/markets/ashare/overview")
    assert resp.status_code == 200
    assert resp.json()["status"] == "warming"


def test_overview_returns_quotes_from_cache():
    cache = get_quote_cache()
    cache._store.clear()
    now = datetime.now(timezone.utc)
    cache.put(Quote(market="ashare", symbol="X1", ts=now, price=Decimal("10"),
                    change_pct=1.5, volume=100, source="t"))
    cache.put(Quote(market="ashare", symbol="X2", ts=now, price=Decimal("20"),
                    change_pct=-2.0, volume=200, source="t"))
    with TestClient(app) as client:
        resp = client.get("/api/markets/ashare/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "ashare"
    assert len(body["quotes"]) >= 2
    assert body["top_gainers"][0]["change_pct"] >= body["top_losers"][0]["change_pct"]
