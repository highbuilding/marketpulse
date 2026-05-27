from datetime import datetime, timezone

import pytest
import fakeredis.aioredis

from core.cache import keys
from core.cache.redis_client import RedisCache
from apps.collector.jobs.market_dashboard import build_dashboard


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_build_dashboard_aggregates_indices_section(cache):
    # 模拟 index_minute job 已写好 cache
    await cache.set_msgpack(
        keys.cache_index_minute("000001.SH", days=1),
        {"symbol": "000001.SH", "granularity": "5m",
         "points": [{"ts": "2026-05-27T01:30:00+00:00", "close": 3000.5, "volume": 1000}],
         "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(), "stale": False}},
        ttl_s=90,
    )
    payload = await build_dashboard("ashare", cache=cache)
    assert payload["market"] == "ashare"
    assert isinstance(payload["indices"], list)
    assert len(payload["indices"]) == 1
    assert payload["indices"][0]["symbol"] == "000001.SH"
    assert "meta" in payload
    assert payload["meta"]["stale"] is False


async def test_build_dashboard_marks_missing_sections_when_indices_absent(cache):
    payload = await build_dashboard("ashare", cache=cache)
    assert "indices" in payload["meta"]["missing_sections"]


async def test_build_dashboard_writes_to_redis_cache(cache):
    payload = await build_dashboard("ashare", cache=cache)
    cached = await cache.get_msgpack(keys.cache_market_dashboard("ashare"))
    assert cached is not None
    assert cached["market"] == "ashare"
