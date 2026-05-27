import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from core.services.kline_service import KLineService
from core.domain.models import Bar


def make_bar(ts: datetime, market: str = "ashare", symbol: str = "600519.SH",
             amount=1000.0, turnover=0.5) -> Bar:
    return Bar(
        market=market, symbol=symbol, ts=ts, interval="1d",
        open=10.0, high=11.0, low=9.0, close=10.5, volume=1000,
        amount=amount, turnover=turnover,
    )


def _make_svc():
    bar_repo = MagicMock()
    adapters = MagicMock()  # dict-like mock; .get() 不应被调用
    return KLineService(bar_repo=bar_repo, adapters=adapters)


async def test_cache_only_returns_partial_when_amount_missing():
    svc = _make_svc()
    bars = [
        make_bar(datetime(2026, 5, 1, tzinfo=timezone.utc), amount=None),
        make_bar(datetime(2026, 5, 2, tzinfo=timezone.utc), amount=None),
    ] + [
        make_bar(datetime(2026, 5, 3 + i, tzinfo=timezone.utc))
        for i in range(0, 23)
    ]
    svc.repo.fetch_history = MagicMock(return_value=bars)
    end = datetime(2026, 5, 27, tzinfo=timezone.utc)
    start = end - timedelta(days=30)
    result, partial = await svc.get_bars_cache_only(
        "600519.SH", interval="1d", start=start, end=end,
    )
    # adapter 不应被调用
    svc.adapters.get.assert_not_called()
    assert len(result) == len(bars)
    # last 20 全是非 None,所以 partial=False
    assert partial is False


async def test_cache_only_returns_empty_when_cache_miss():
    svc = _make_svc()
    svc.repo.fetch_history = MagicMock(return_value=[])
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    result, partial = await svc.get_bars_cache_only(
        "600519.SH", interval="1d", start=start, end=end,
    )
    assert result == []
    assert partial is False
    svc.adapters.get.assert_not_called()


async def test_cache_only_intraday_returns_data_without_adapter():
    svc = _make_svc()
    bars = [
        Bar(market="ashare", symbol="600519.SH",
            ts=datetime.now(timezone.utc) - timedelta(hours=i),
            interval="60m", open=10, high=11, low=9, close=10.5, volume=100,
            amount=1000.0, turnover=0.5)
        for i in range(20)
    ]
    svc.repo.fetch_history = MagicMock(return_value=bars)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2)
    result, partial = await svc.get_bars_cache_only(
        "600519.SH", interval="60m", start=start, end=end,
    )
    svc.adapters.get.assert_not_called()
    # adapter 不被调用,且无异常
    assert isinstance(result, list)
