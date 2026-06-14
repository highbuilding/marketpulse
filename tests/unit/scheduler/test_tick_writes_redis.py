import pytest
import fakeredis.aioredis
from datetime import datetime, timezone
import json

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.models import Quote
from core.scheduler.jobs import write_quote_to_redis


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_write_quote_to_redis_msgpack_payload(cache):
    q = Quote(market="ashare", symbol="600519.SH", price=1234.5,
              change_pct=1.2, volume=100,
              ts=datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc),
              source="test")
    await write_quote_to_redis(q, cache=cache)
    payload = await cache.get_msgpack(keys.cache_quote("ashare", "600519.SH"))
    assert payload is not None
    assert payload["symbol"] == "600519.SH"
    assert payload["price"] == 1234.5
    assert payload["change_pct"] == 1.2
    assert payload["volume"] == 100
    assert payload["ts"] == "2026-05-27T01:00:00+00:00"
    assert payload["market"] == "ashare"

    entries = await cache._r.xrange(keys.BUS_QUOTE_TICK)  # noqa: SLF001
    assert len(entries) == 1
    raw = entries[0][1][b"data"]
    event = json.loads(raw)
    assert event["market"] == "ashare"
    assert event["symbol"] == "600519.SH"
    assert event["amount"] is None


async def test_write_quote_to_redis_swallows_errors(cache, monkeypatch):
    async def raise_set(*args, **kwargs):
        raise RuntimeError("redis down")
    monkeypatch.setattr(cache, "set_msgpack", raise_set)
    q = Quote(market="ashare", symbol="X.SH", price=1.0, change_pct=0.0,
              volume=0, ts=datetime.now(timezone.utc), source="test")
    # 不应抛
    await write_quote_to_redis(q, cache=cache)
