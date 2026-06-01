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


from decimal import Decimal as _D
from datetime import datetime as _dt, timezone as _tz
from core.domain.models import Bar
from apps.collector.ashare.quote_bar_ticker import seed_baseline


def test_seed_baseline_from_smaller_bars():
    bars = [
        Bar(market="ashare", symbol="X", ts=_dt(2026,6,1,5,5,tzinfo=_tz.utc),
            open=_D("10"), high=_D("12"), low=_D("9"), close=_D("11"),
            volume=100, interval="5m"),
        Bar(market="ashare", symbol="X", ts=_dt(2026,6,1,5,10,tzinfo=_tz.utc),
            open=_D("11"), high=_D("15"), low=_D("10"), close=_D("14"),
            volume=200, interval="5m"),
    ]
    st = seed_baseline(bars)
    assert st.open == _D("10")
    assert st.high == _D("15")
    assert st.low == _D("9")
    assert st.close == _D("14")
    assert st.volume == 300


def test_seed_baseline_empty_returns_none():
    assert seed_baseline([]) is None
