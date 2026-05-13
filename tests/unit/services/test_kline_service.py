from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.models import Bar
from core.services.kline_service import KLineService


def _bar(symbol, day_offset, interval="1d", close=100.0):
    ts = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return Bar(
        market="ashare", symbol=symbol, ts=ts,
        open=Decimal(str(close - 1)), high=Decimal(str(close + 2)),
        low=Decimal(str(close - 2)), close=Decimal(str(close)),
        volume=1_000_000, interval=interval,
    )


@pytest.mark.asyncio
async def test_get_bars_cache_hit_returns_from_duckdb():
    repo = MagicMock()
    repo.fetch_history.return_value = [_bar("600519.SH", i, close=100 + i) for i in range(10)]
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock()
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "600519.SH",
        interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert len(bars) == 10
    adapter.fetch_history.assert_not_called()


@pytest.mark.asyncio
async def test_get_bars_cache_miss_calls_adapter_then_writes_back():
    repo = MagicMock()
    repo.fetch_history.return_value = []
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock(return_value=[_bar("X", i) for i in range(5)])
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "X", interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )
    adapter.fetch_history.assert_called_once()
    repo.insert_bars.assert_called_once()
    assert len(bars) == 5


@pytest.mark.asyncio
async def test_get_bars_weekly_resamples_daily():
    repo = MagicMock()
    daily = [_bar("X", i, close=100 + i) for i in range(14)]
    repo.fetch_history.return_value = daily
    adapter = MagicMock()
    svc = KLineService(repo, adapter)
    weeks = await svc.get_bars(
        "X", interval="1wk",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert 1 <= len(weeks) <= 3
    assert all(b.interval == "1wk" for b in weeks)


@pytest.mark.asyncio
async def test_get_intraday_calls_adapter_intraday_and_writes():
    repo = MagicMock()
    repo.fetch_history.return_value = []
    intraday_bar = Bar(
        market="ashare", symbol="X",
        ts=datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
        open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
        close=Decimal("100"), volume=1000, interval="5m",
    )
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[intraday_bar])
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "X", interval="5m",
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 23, tzinfo=timezone.utc),
    )
    adapter.fetch_intraday.assert_called_once_with("X", freq="5")
    repo.insert_bars.assert_called_once()
    assert bars[0].interval == "5m"
