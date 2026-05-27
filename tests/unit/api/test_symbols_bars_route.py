import pytest
import fakeredis.aioredis
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from apps.api.main import app
from apps.api.deps import get_redis_cache, get_kline_service
from core.cache.redis_client import RedisCache
from core.domain.models import Bar


@pytest.fixture
def client():
    return TestClient(app)


async def test_bars_returns_bars_when_cache_only_hit(client):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    svc = MagicMock()
    bars = [
        Bar(market="ashare", symbol="600519.SH",
            ts=datetime(2026, 5, 1, tzinfo=timezone.utc), interval="1d",
            open=10, high=11, low=9, close=10.5, volume=100,
            amount=1000.0, turnover=0.5)
    ]
    svc.get_bars_cache_only = AsyncMock(return_value=(bars, False))

    app.dependency_overrides[get_redis_cache] = lambda: cache
    app.dependency_overrides[get_kline_service] = lambda: svc

    try:
        r = client.get("/api/symbols/600519.SH/bars?interval=1d&days=30")
        assert r.status_code == 200
        data = r.json()
        assert len(data["bars"]) == 1
        assert data["meta"]["partial"] is False
        assert data["meta"]["stale"] is False
    finally:
        app.dependency_overrides.clear()
        await fake.aclose()


async def test_bars_returns_stale_when_cache_miss_and_publishes_refill(client):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    svc = MagicMock()
    svc.get_bars_cache_only = AsyncMock(return_value=([], False))

    app.dependency_overrides[get_redis_cache] = lambda: cache
    app.dependency_overrides[get_kline_service] = lambda: svc

    try:
        r = client.get("/api/symbols/600519.SH/bars?interval=1d&days=30")
        assert r.status_code == 200
        data = r.json()
        assert data["bars"] == []
        assert data["meta"]["stale"] is True
        # 验证发了 refill 请求到 stream
        length = await fake.xlen("bus:bars.refill_request")
        assert length >= 1
    finally:
        app.dependency_overrides.clear()
        await fake.aclose()


async def test_bars_partial_flag_when_metrics_missing(client):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    svc = MagicMock()
    bars = [Bar(market="ashare", symbol="X.SH",
                ts=datetime.now(timezone.utc), interval="1d",
                open=10, high=11, low=9, close=10.5, volume=100,
                amount=None, turnover=None)]
    svc.get_bars_cache_only = AsyncMock(return_value=(bars, True))

    app.dependency_overrides[get_redis_cache] = lambda: cache
    app.dependency_overrides[get_kline_service] = lambda: svc

    try:
        r = client.get("/api/symbols/X.SH/bars?interval=1d&days=30")
        assert r.status_code == 200
        assert r.json()["meta"]["partial"] is True
    finally:
        app.dependency_overrides.clear()
        await fake.aclose()
