import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.ashare.quote_bar_ticker import QuoteBarTicker


@pytest.mark.asyncio
async def test_tick_once_publishes_final_false():
    redis = MagicMock()
    redis._r = MagicMock()
    redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value={
        "price": "100.5", "volume": 1000, "amount": 100500.0})
    t = QuoteBarTicker(redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)  # 13:50:30 BJT 开市
    await t.tick_once("600519.SH", "5m", now=now)
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is False
    assert payload["interval"] == "5m"
    assert payload["symbol"] == "600519.SH"
    assert redis.set_msgpack.await_count == 1


@pytest.mark.asyncio
async def test_tick_once_skips_when_no_quote():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value=None)
    t = QuoteBarTicker(redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)
    await t.tick_once("600519.SH", "5m", now=now)
    assert redis._r.xadd.await_count == 0


@pytest.mark.asyncio
async def test_tick_once_skips_outside_session():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value={"price": "100", "volume": 1})
    t = QuoteBarTicker(redis)
    now = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)  # 16:00 BJT 收盘后
    await t.tick_once("600519.SH", "5m", now=now)
    assert redis._r.xadd.await_count == 0
