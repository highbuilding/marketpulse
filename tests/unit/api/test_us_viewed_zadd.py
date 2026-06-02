"""TDD: sse_bars _register_subscriptions 对美股标的写 state:us:viewed ZSET."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.routes.sse_bars import _register_subscriptions


@pytest.mark.asyncio
async def test_us_symbol_zadds_viewed():
    rc = MagicMock()
    rc._r = MagicMock()
    rc._r.set = AsyncMock()
    rc._r.zadd = AsyncMock()
    await _register_subscriptions({"AAPL"}, "5m", rc)
    rc._r.zadd.assert_awaited()
    assert rc._r.zadd.await_args[0][0] == "state:us:viewed"


@pytest.mark.asyncio
async def test_ashare_symbol_no_zadd():
    rc = MagicMock()
    rc._r = MagicMock()
    rc._r.set = AsyncMock()
    rc._r.zadd = AsyncMock()
    await _register_subscriptions({"600519.SH"}, "5m", rc)
    rc._r.zadd.assert_not_awaited()
