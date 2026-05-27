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


async def test_index_minute_returns_cached_payload(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_index_minute("000001.SH", days=1),
        {"symbol": "000001.SH", "granularity": "5m",
         "points": [{"ts": "2026-05-27T01:30:00+00:00", "close": 3000.5, "volume": 1000}],
         "meta": {"fresh_at": "2026-05-27T01:30:00+00:00", "stale": False, "source": "sina"}},
        ttl_s=90,
    )
    r = client.get("/api/indices/000001.SH/minute?days=1")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "000001.SH"
    assert len(data["points"]) == 1
    assert data["meta"]["stale"] is False


async def test_index_minute_returns_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/indices/000001.SH/minute?days=1")
    assert r.status_code == 200
    data = r.json()
    assert data["points"] == []
    assert data["meta"]["stale"] is True


async def test_index_minute_unknown_symbol_404(client):
    r = client.get("/api/indices/UNKNOWN.SH/minute")
    assert r.status_code == 404
