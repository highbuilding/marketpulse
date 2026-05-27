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


async def test_dashboard_returns_cached(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_market_dashboard("ashare"),
        {"market": "ashare", "indices": [], "overview": None,
         "north_flow": None, "hot_sectors": None,
         "meta": {"fresh_at": "2026-05-27T01:00:00+00:00",
                  "stale": False, "missing_sections": []}},
        ttl_s=120,
    )
    r = client.get("/api/markets/ashare/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert data["market"] == "ashare"
    assert data["meta"]["stale"] is False


async def test_dashboard_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/markets/ashare/dashboard")
    assert r.status_code == 200
    assert r.json()["meta"]["stale"] is True


async def test_dashboard_unknown_market_404(client):
    r = client.get("/api/markets/xx/dashboard")
    assert r.status_code == 404
