from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.models import Bar
from core.services.volume_indicator_service import VolumeIndicatorService


def _bar(
    i: int,
    close: float,
    volume: int,
    amount: float | None = None,
    interval: str = "1d",
) -> Bar:
    return Bar(
        market="ashare",
        symbol="002415.SZ",
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=i),
        open=Decimal(str(close - 1)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 2)),
        close=Decimal(str(close)),
        volume=volume,
        interval=interval,
        amount=amount,
        turnover=2.5,
    )


def test_volume_indicators_compute_ma_ratio_and_obv():
    bars = [_bar(i, 10 + i, 1000, 10_000) for i in range(20)]
    bars.append(_bar(20, 35, 3000, 30_000))
    rows = VolumeIndicatorService().compute(bars)
    latest = rows[-1]
    assert latest.vol_ma5 == pytest.approx((1000 * 4 + 3000) / 5)
    assert latest.vol_ma20 == pytest.approx((1000 * 19 + 3000) / 20)
    assert latest.volume_ratio == pytest.approx(3.0)
    assert latest.single_bar_volume_ratio == pytest.approx(3.0)
    assert latest.obv > rows[-2].obv
    assert latest.is_volume_breakout is True
    assert latest.amount_ma20 is not None


def test_intraday_volume_ratio_uses_previous_five_days_same_progress():
    bars: list[Bar] = []
    for day in range(5):
        bars.append(_bar(day, 10, 100, interval="15m"))
        bars.append(_bar(day, 11, 200, interval="15m"))
    bars.append(_bar(5, 12, 300, interval="15m"))

    latest = VolumeIndicatorService().compute(bars)[-1]

    assert latest.volume_ratio == pytest.approx(3.0)
    assert latest.single_bar_volume_ratio is None


def test_volume_indicators_empty_returns_empty():
    assert VolumeIndicatorService().compute([]) == []
