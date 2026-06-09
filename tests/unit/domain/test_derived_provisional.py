"""周/月线进行中根合成 synthesize_provisional 单测。

固化方案A口径: 本周/本月已收线日线 + 今日实时价 → 当周/当月进行中根,
与 daily_settlement 收盘后 resample (W-FRI/ME) 同口径。
"""
from datetime import datetime, timezone
from decimal import Decimal

from core.domain.derived_provisional import synthesize_provisional
from core.domain.models import Bar


def _d(ts: datetime, o, h, l, c, v=100) -> Bar:
    return Bar(
        market="ashare", symbol="600519.SH", ts=ts,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
        close=Decimal(str(c)), volume=v, interval="1d",
    )


def test_weekly_provisional_aggregates_week_with_live_price():
    # 本周一/二已收线日线(ts=UTC自然日, 周一6-8=UTC6-7 16:00...简化用naive UTC日期)
    # 用 6-8(周一)、6-9(周二)收线 + 今日6-10(周三)实时价
    daily = [
        _d(datetime(2026, 6, 8, tzinfo=timezone.utc), 100, 110, 95, 105),
        _d(datetime(2026, 6, 9, tzinfo=timezone.utc), 105, 115, 102, 108),
    ]
    bar = synthesize_provisional(
        daily, market="ashare", symbol="600519.SH", target_iv="1wk",
        today_ts=datetime(2026, 6, 10, tzinfo=timezone.utc), price=120.0, volume=50,
    )
    assert bar is not None
    assert bar.interval == "1wk"
    # 当周 OHLC: open=周一开100, high=max(110,115,120)=120, low=min(95,102,120)=95, close=实时120
    assert float(bar.open) == 100
    assert float(bar.high) == 120  # 实时价创周内新高
    assert float(bar.low) == 95
    assert float(bar.close) == 120  # 实时价


def test_monthly_provisional_aggregates_month():
    daily = [
        _d(datetime(2026, 6, 3, tzinfo=timezone.utc), 100, 130, 90, 120),
        _d(datetime(2026, 6, 4, tzinfo=timezone.utc), 120, 125, 115, 118),
    ]
    bar = synthesize_provisional(
        daily, market="ashare", symbol="600519.SH", target_iv="1mo",
        today_ts=datetime(2026, 6, 5, tzinfo=timezone.utc), price=110.0,
    )
    assert bar is not None
    assert bar.interval == "1mo"
    assert float(bar.open) == 100
    assert float(bar.high) == 130  # 月内最高
    assert float(bar.low) == 90
    assert float(bar.close) == 110


def test_today_price_overrides_existing_today_bar():
    # 收盘后场景: daily 已含今日日线, 实时价应覆盖(不重复计入)
    daily = [
        _d(datetime(2026, 6, 8, tzinfo=timezone.utc), 100, 110, 95, 105),
        _d(datetime(2026, 6, 10, tzinfo=timezone.utc), 108, 112, 106, 109),  # 今日已收线
    ]
    bar = synthesize_provisional(
        daily, market="ashare", symbol="600519.SH", target_iv="1wk",
        today_ts=datetime(2026, 6, 10, tzinfo=timezone.utc), price=120.0,
    )
    assert bar is not None
    # 今日那根被实时价覆盖: high 不含旧的112, 用实时120; low=min(95, 120)=95
    assert float(bar.high) == 120
    assert float(bar.close) == 120


def test_unknown_interval_returns_none():
    bar = synthesize_provisional(
        [], market="ashare", symbol="X", target_iv="5m",
        today_ts=datetime(2026, 6, 10, tzinfo=timezone.utc), price=1.0,
    )
    assert bar is None


def test_only_today_price_no_history():
    # 无历史日线, 仅今日实时价 → 当周进行中根 = 单根
    bar = synthesize_provisional(
        [], market="ashare", symbol="600519.SH", target_iv="1wk",
        today_ts=datetime(2026, 6, 10, tzinfo=timezone.utc), price=120.0,
    )
    assert bar is not None
    assert float(bar.open) == 120
    assert float(bar.close) == 120
