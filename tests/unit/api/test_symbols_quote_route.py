import pytest
import fakeredis.aioredis
from datetime import datetime, timezone
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


async def test_quote_returns_cached_payload(client, patched_cache):
    fresh_ts = datetime.now(timezone.utc).isoformat()
    await patched_cache.set_msgpack(
        keys.cache_quote("ashare", "600519.SH"),
        {"market": "ashare", "symbol": "600519.SH", "price": 1234.5,
         "change_pct": 1.2, "volume": 100,
         "ts": fresh_ts},
        ttl_s=90,
    )
    r = client.get("/api/symbols/600519.SH/quote")
    assert r.status_code == 200
    data = r.json()
    assert data["price"] == 1234.5
    assert data["change_pct"] == 1.2
    assert data["meta"]["stale"] is False


async def test_quote_returns_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/symbols/NOTEXIST.SH/quote")
    assert r.status_code == 200
    data = r.json()
    assert data["price"] is None
    assert data["meta"]["stale"] is True
