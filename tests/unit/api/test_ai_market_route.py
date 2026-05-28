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


def _full_payload() -> dict:
    return {
        "generated_at": "2026-05-28T01:00:00+00:00",
        "market": "ashare",
        "indices": [],
        "breadth": {
            "total": 5000, "advancers": 3000, "decliners": 1800, "flat": 200,
            "up_limit": 30, "down_limit": 10, "total_amount": 1.2e12,
            "up_ratio": 0.6, "down_ratio": 0.36, "net_width": 1200,
        },
        "top_gainers": [{"symbol": "A.SH", "name": "A", "price": 10.0,
                         "change_pct": 9.9, "volume": 100, "amount": 1000.0}],
        "top_losers": [],
        "hot_sectors": [],
        "weak_sectors": [],
        "watchlist": [],
        "index_strength": {"ranking": [], "small_vs_large_pct": None,
                           "growth_vs_large_pct": None},
        "events": [],
        "ai_brief": {},
        "degraded": [],
        "meta": {"fresh_at": "2026-05-28T01:00:00+00:00", "stale": False},
    }


async def test_market_packet_returns_cached(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_market_ai_packet("ashare"),
        _full_payload(), ttl_s=240,
    )
    r = client.get("/api/ai/ashare/market-packet")
    assert r.status_code == 200
    data = r.json()
    assert data["market"] == "ashare"
    assert data["meta"]["stale"] is False
    assert data["breadth"]["total"] == 5000
    assert len(data["top_gainers"]) == 1
    assert data["top_gainers"][0]["symbol"] == "A.SH"


async def test_market_packet_returns_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/ai/ashare/market-packet")
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["stale"] is True
    assert data["meta"]["reason"] == "warming_up"
    assert data["top_gainers"] == []
    assert data["breadth"]["total"] == 0
