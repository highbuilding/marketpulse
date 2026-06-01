import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.ws_consumer import _desired_trade_symbols, _subscription_deltas


def test_subscription_deltas():
    add, remove = _subscription_deltas(desired={"AAPL", "MSFT"}, subscribed={"MSFT", "TSLA"})
    assert add == {"AAPL"}
    assert remove == {"TSLA"}


@pytest.mark.asyncio
async def test_desired_trade_symbols_scans_and_caps():
    redis = MagicMock(); redis._r = MagicMock()
    # 模拟 scan 一次返回所有 key, cursor 归 0
    keys = [f"state:subscribe:us:SYM{i}:5m".encode() for i in range(40)]
    redis._r.scan = AsyncMock(return_value=(0, keys))
    got = await _desired_trade_symbols(redis, cap=30)
    assert len(got) == 30                       # 上限 30
    assert all(s.startswith("SYM") for s in got)


@pytest.mark.asyncio
async def test_desired_trade_symbols_ignores_other_markets():
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.scan = AsyncMock(return_value=(0, [b"state:subscribe:us:AAPL:5m"]))
    got = await _desired_trade_symbols(redis, cap=30)
    assert got == {"AAPL"}
