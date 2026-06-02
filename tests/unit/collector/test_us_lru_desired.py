"""TDD: ws_consumer _desired_trade_symbols 改为 LRU ZSET + realtime_active 维护."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.ws_consumer import _desired_trade_symbols


@pytest.mark.asyncio
async def test_desired_reads_zset_top_n_and_sets_active():
    redis = MagicMock()
    redis._r = MagicMock()
    redis._r.zremrangebyscore = AsyncMock()
    redis._r.zrevrange = AsyncMock(return_value=[b"AAPL", b"NVDA", b"MSFT"])
    redis._r.delete = AsyncMock()
    redis._r.sadd = AsyncMock()
    got = await _desired_trade_symbols(redis, cap=30)
    assert got == {"AAPL", "NVDA", "MSFT"}
    a = redis._r.zrevrange.await_args
    assert a[0][0] == "state:us:viewed" and a[0][1] == 0 and a[0][2] == 29
    redis._r.sadd.assert_awaited()


@pytest.mark.asyncio
async def test_desired_empty_returns_empty_and_clears_active():
    redis = MagicMock()
    redis._r = MagicMock()
    redis._r.zremrangebyscore = AsyncMock()
    redis._r.zrevrange = AsyncMock(return_value=[])
    redis._r.delete = AsyncMock()
    redis._r.sadd = AsyncMock()
    got = await _desired_trade_symbols(redis, cap=30)
    assert got == set()
    redis._r.delete.assert_awaited()
