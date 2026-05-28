from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import fakeredis.aioredis

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.services.market_query import RankRow
from apps.collector.jobs.market_top import refresh_market_top


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


def _row(symbol: str, change_pct: float) -> RankRow:
    return RankRow(
        symbol=symbol, name=symbol, price=10.0,
        change_pct=change_pct, volume=1000, amount=10000.0,
    )


async def test_refresh_market_top_writes_cache(cache):
    svc = AsyncMock()
    svc.top_ashare = AsyncMock(side_effect=[
        [_row("A.SH", 9.9), _row("B.SH", 8.0)],
        [_row("X.SH", -9.5), _row("Y.SH", -7.0)],
    ])
    await refresh_market_top("ashare", svc=svc, cache=cache, limit=2)
    payload = await cache.get_msgpack(keys.cache_market_top("ashare"))
    assert payload is not None
    assert payload["market"] == "ashare"
    assert len(payload["gainers"]) == 2
    assert len(payload["losers"]) == 2
    assert payload["gainers"][0]["symbol"] == "A.SH"
    assert payload["meta"]["stale"] is False


async def test_refresh_market_top_swallows_failure(cache):
    svc = AsyncMock()
    svc.top_ashare = AsyncMock(side_effect=RuntimeError("ak timeout"))
    # 不应抛, 不写 cache
    await refresh_market_top("ashare", svc=svc, cache=cache, limit=10)
    payload = await cache.get_msgpack(keys.cache_market_top("ashare"))
    assert payload is None


async def test_refresh_market_top_unsupported_market_no_call(cache):
    svc = AsyncMock()
    await refresh_market_top("us", svc=svc, cache=cache)
    svc.top_ashare.assert_not_called()
    svc.top_hk.assert_not_called()
