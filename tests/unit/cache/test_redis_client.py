import pytest
import fakeredis.aioredis

from core.cache import keys
from core.cache.redis_client import RedisCache


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_set_and_get_msgpack_roundtrip(cache):
    payload = {"symbol": "600519.SH", "price": 1234.56, "ts": "2026-05-27T08:00:00Z"}
    await cache.set_msgpack(keys.cache_quote("ashare", "600519.SH"), payload, ttl_s=60)
    got = await cache.get_msgpack(keys.cache_quote("ashare", "600519.SH"))
    assert got == payload


async def test_get_msgpack_missing_returns_none(cache):
    got = await cache.get_msgpack(keys.cache_quote("ashare", "NOTEXIST.SH"))
    assert got is None


async def test_set_msgpack_validates_key(cache):
    with pytest.raises(ValueError):
        await cache.set_msgpack("invalid_key_no_namespace", {"x": 1}, ttl_s=60)


async def test_set_msgpack_requires_positive_ttl(cache):
    with pytest.raises(ValueError, match="ttl_s must be > 0"):
        await cache.set_msgpack(keys.cache_quote("ashare", "X.SH"), {}, ttl_s=0)


async def test_ttl_is_set(cache):
    key = keys.cache_quote("ashare", "TTL.SH")
    await cache.set_msgpack(key, {"x": 1}, ttl_s=30)
    ttl = await cache.ttl(key)
    assert 25 < ttl <= 30


async def test_ping(cache):
    assert await cache.ping() is True
