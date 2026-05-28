import pytest
import fakeredis.aioredis
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.deps import get_redis_cache
from core.cache.redis_client import RedisCache
from core.cache import keys


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
async def patched_cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    app.dependency_overrides[get_redis_cache] = lambda: cache
    yield cache
    await fake.aclose()
    app.dependency_overrides.clear()


async def test_top_returns_cached_payload(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_market_top("ashare"),
        {
            "market": "ashare",
            "gainers": [{"symbol": "A.SH", "name": "A", "price": 10.0,
                         "change_pct": 9.9, "volume": 100, "amount": 1000.0}],
            "losers": [{"symbol": "X.SH", "name": "X", "price": 5.0,
                        "change_pct": -9.0, "volume": 50, "amount": 250.0}],
            "meta": {"fresh_at": "2026-05-28T01:00:00+00:00",
                     "stale": False, "limit": 10},
        },
        ttl_s=180,
    )
    r = client.get("/api/markets/ashare/top?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["market"] == "ashare"
    assert len(data["gainers"]) == 1
    assert data["gainers"][0]["symbol"] == "A.SH"
    assert data["meta"]["stale"] is False


async def test_top_returns_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/markets/ashare/top?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["gainers"] == []
    assert data["losers"] == []
    assert data["meta"]["stale"] is True
    assert data["meta"]["reason"] == "warming_up"


async def test_top_unsupported_market_404(client):
    r = client.get("/api/markets/us/top?limit=10")
    assert r.status_code == 404


async def test_top_limit_truncates(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_market_top("ashare"),
        {
            "market": "ashare",
            "gainers": [{"symbol": f"S{i}.SH", "name": f"S{i}", "price": 10.0,
                         "change_pct": float(i), "volume": 100, "amount": 1000.0}
                        for i in range(20)],
            "losers": [],
            "meta": {"fresh_at": "now", "stale": False, "limit": 20},
        },
        ttl_s=180,
    )
    r = client.get("/api/markets/ashare/top?limit=5")
    assert r.status_code == 200
    assert len(r.json()["gainers"]) == 5
