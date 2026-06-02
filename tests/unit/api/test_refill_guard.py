"""refill 白名单(CORE∪watchlist)+ inflight 去重 单测。"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.routes.symbols import _publish_refill_request


def _redis():
    r = MagicMock()
    r._r = MagicMock()
    r._r.xadd = AsyncMock()
    r._r.set = AsyncMock(return_value=True)   # NX 成功(未在途)
    return r


@pytest.mark.asyncio
async def test_core_symbol_allowed():
    redis = _redis()
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=[])
    await _publish_refill_request(redis, "AAPL", "5m", 365, watchlist=wl)
    redis._r.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchlist_symbol_allowed():
    redis = _redis()
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["ZZZZ.SH"])
    await _publish_refill_request(redis, "ZZZZ.SH", "5m", 365, watchlist=wl)
    redis._r.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_cold_symbol_blocked():
    redis = _redis()
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=[])
    await _publish_refill_request(redis, "GARBAGE123", "5m", 365, watchlist=wl)
    redis._r.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_inflight_blocked():
    redis = _redis()
    redis._r.set = AsyncMock(return_value=None)   # NX 失败(已在途)
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=[])
    await _publish_refill_request(redis, "AAPL", "5m", 365, watchlist=wl)
    redis._r.xadd.assert_not_awaited()
