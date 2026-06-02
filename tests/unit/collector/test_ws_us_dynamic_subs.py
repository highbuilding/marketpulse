import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.ws_consumer import _desired_trade_symbols, _subscription_deltas


def test_subscription_deltas():
    add, remove = _subscription_deltas(desired={"AAPL", "MSFT"}, subscribed={"MSFT", "TSLA"})
    assert add == {"AAPL"}
    assert remove == {"TSLA"}


@pytest.mark.asyncio
async def test_desired_trade_symbols_scans_and_caps():
    """LRU ZSET: zrevrange 返回超过 cap 时截断到 cap。"""
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.zremrangebyscore = AsyncMock()
    redis._r.delete = AsyncMock()
    members = [f"SYM{i}".encode() for i in range(40)]
    redis._r.zrevrange = AsyncMock(return_value=members[:30])  # Redis 侧已截断
    redis._r.sadd = AsyncMock()
    got = await _desired_trade_symbols(redis, cap=30)
    assert len(got) == 30                       # 上限 30
    assert all(s.startswith("SYM") for s in got)


@pytest.mark.asyncio
async def test_desired_trade_symbols_ignores_other_markets():
    """LRU ZSET: 直接从 ZSET 取 symbol 字符串, 不再 scan key 解析 market。"""
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.zremrangebyscore = AsyncMock()
    redis._r.delete = AsyncMock()
    redis._r.zrevrange = AsyncMock(return_value=[b"AAPL"])
    redis._r.sadd = AsyncMock()
    got = await _desired_trade_symbols(redis, cap=30)
    assert got == {"AAPL"}
