from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest

from core.domain.models import Bar
from core.services.daily_review_builder import (
    DailyReviewBuilder,
    _max_drawdown,
    _position_ratio,
    _split_tiers,
    _trend_segments,
)


def test_position_ratio_basic():
    assert _position_ratio(10, 0, 20) == 0.5
    assert _position_ratio(0, 0, 20) == 0.0
    assert _position_ratio(20, 0, 20) == 1.0
    # 区间退化(高=低)返回 None
    assert _position_ratio(5, 5, 5) is None
    # 越界裁剪
    assert _position_ratio(-5, 0, 20) == 0.0


def test_max_drawdown():
    # 100 -> 120 -> 90: 峰值 120, 回撤到 90 = -25%
    assert _max_drawdown([100, 120, 90]) == pytest.approx(-0.25)
    # 单调上涨无回撤
    assert _max_drawdown([100, 110, 120]) == 0.0


def test_trend_segments_detects_up_down():
    # 先涨 20% 再跌 20%
    closes = [100, 105, 110, 115, 120, 110, 100, 96]
    segs = _trend_segments(closes, swing_pct=8.0)
    kinds = [s["kind"] for s in segs]
    assert "上涨" in kinds
    assert any(s["change_pct"] > 0 for s in segs)


def test_split_tiers_proportions():
    scored = [{"symbol": f"s{i}", "score": 100 - i} for i in range(10)]
    tiers = _split_tiers(scored)
    # 龙头取前 20%, 杂毛取后 30%
    assert tiers["leaders"][0]["symbol"] == "s0"
    assert len(tiers["leaders"]) == 2
    assert len(tiers["laggards"]) == 3
    assert tiers["laggards"][-1]["symbol"] == "s9"


def test_split_tiers_small_group():
    scored = [{"symbol": "a", "score": 3}, {"symbol": "b", "score": 2}]
    tiers = _split_tiers(scored)
    assert tiers["leaders"][0]["symbol"] == "a"
    assert tiers["laggards"] == []


class _FakeBarRepo:
    """按 symbol 返回构造的日线序列。"""

    def __init__(self, series: dict[str, list[float]]):
        self.series = series

    def fetch_history(self, market, symbol, start, end, interval="1d", *, closed_only=False):
        closes = self.series.get(symbol, [])
        base = datetime(2026, 1, 2, tzinfo=timezone.utc)
        bars = []
        for i, c in enumerate(closes):
            bars.append(
                Bar(
                    market=market, symbol=symbol,
                    ts=base + timedelta(days=i),
                    open=Decimal(str(c)), high=Decimal(str(c * 1.01)),
                    low=Decimal(str(c * 0.99)), close=Decimal(str(c)),
                    volume=1000, interval=interval, amount=float(c * 1000),
                )
            )
        return bars


class _FakeSwRepo:
    async def list_info(self):
        return []

    async def list_history(self, *args, **kwargs):
        return []


class _FakeThemeRepo:
    async def list_definitions(self, *args, **kwargs):
        return []

    async def list_static_constituents(self, *args, **kwargs):
        return []


@pytest.mark.asyncio
async def test_market_trend_section_ytd():
    # 沪深300 年内从 100 涨到 120 (+20%)
    series = {"000300.SH": [100 + i for i in range(0, 21)]}  # 100..120
    builder = DailyReviewBuilder(
        _FakeBarRepo(series), _FakeSwRepo(), _FakeThemeRepo())
    result = await builder.build("2026-06-16")
    trend = next((s for s in result.sections if s.key == "market_trend"), None)
    assert trend is not None
    bench = next(r for r in trend.evidence["indices"] if r["symbol"] == "000300.SH")
    assert bench["ytd_change_pct"] == pytest.approx(20.0, abs=0.1)
    assert "年内" in trend.label
