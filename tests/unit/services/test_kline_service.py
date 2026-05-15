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


# ============== 缓存覆盖检查 (回归 600004 白云机场 4 条窄窗口问题) ==============

@pytest.mark.asyncio
async def test_get_bars_refetches_when_cache_too_narrow():
    """模拟 DuckDB 里有旧的小窗口数据(几根),请求大窗口时应重拉。"""
    repo = MagicMock()
    # 旧 cache 只有 4 条,日期都在 2026-05 月底
    old_bars = [_bar("600004.SH", i, close=10 + i) for i in range(28, 32)]  # ts 2026-05-29 → 06-01
    fresh_bars = [_bar("600004.SH", i, close=10 + i) for i in range(0, 30)]
    repo.fetch_history.return_value = old_bars
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock(return_value=fresh_bars)
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "600004.SH", interval="1d",
        start=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 30, tzinfo=timezone.utc),
    )
    # cache 没覆盖 2020,必须重拉
    adapter.fetch_history.assert_called_once()
    assert len(bars) == 30


@pytest.mark.asyncio
async def test_get_bars_cache_covers_returns_cached():
    """缓存覆盖范围足够,不再调 adapter。"""
    repo = MagicMock()
    # cache 覆盖了 5-1 到 5-30 之间 30 条,start=5-7, end=5-28 → 完全覆盖
    cached = [_bar("X", i, close=100) for i in range(30)]
    repo.fetch_history.return_value = cached
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock()
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "X", interval="1d",
        start=datetime(2026, 5, 7, tzinfo=timezone.utc),
        end=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    adapter.fetch_history.assert_not_called()
    assert len(bars) == 30


@pytest.mark.asyncio
async def test_get_bars_cache_head_too_late_triggers_refetch():
    """缓存起点比请求起点晚太多(>7d),需重拉。"""
    repo = MagicMock()
    # cache 第一根 ts=2026-05-01,请求 start=2026-04-01 → 早了 30 天 → 不覆盖
    cached = [_bar("X", i, close=100) for i in range(10)]
    repo.fetch_history.return_value = cached
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock(return_value=[_bar("X", i) for i in range(60)])
    svc = KLineService(repo, adapter)
    await svc.get_bars(
        "X", interval="1d",
        start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    adapter.fetch_history.assert_called_once()


@pytest.mark.asyncio
async def test_get_bars_cache_tail_too_old_triggers_refetch():
    """缓存末点离 end 太远(>4d),需重拉。"""
    repo = MagicMock()
    # cache 最后 ts=2026-05-09,请求 end=2026-05-20 → tail 早 11 天
    cached = [_bar("X", i, close=100) for i in range(9)]
    repo.fetch_history.return_value = cached
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock(return_value=[_bar("X", i) for i in range(20)])
    svc = KLineService(repo, adapter)
    await svc.get_bars(
        "X", interval="1d",
        start=datetime(2026, 4, 28, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    adapter.fetch_history.assert_called_once()


@pytest.mark.asyncio
async def test_get_intraday_5m_cache_too_narrow_refetches():
    """5m intraday cache 范围不够,需重拉 + 写 cache."""
    repo = MagicMock()
    repo.fetch_history.return_value = [
        Bar(market="ashare", symbol="X",
            ts=datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
            open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
            close=Decimal("100"), volume=1000, interval="5m"),
    ]
    fresh = [
        Bar(market="ashare", symbol="X",
            ts=datetime(2026, 5, 11, tzinfo=timezone.utc) + timedelta(hours=i),
            open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
            close=Decimal("100"), volume=1000, interval="5m")
        for i in range(72)
    ]
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=fresh)
    svc = KLineService(repo, adapter)
    await svc.get_bars(
        "X", interval="5m",
        start=datetime(2026, 5, 11, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 23, tzinfo=timezone.utc),
    )
    adapter.fetch_intraday.assert_called_once()
    repo.insert_bars.assert_called_once()


@pytest.mark.asyncio
async def test_get_intraday_1m_never_caches():
    """1m 永远不写 cache,即使有也忽略."""
    repo = MagicMock()
    repo.fetch_history.return_value = []
    intraday_bar = Bar(
        market="ashare", symbol="X",
        ts=datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
        open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
        close=Decimal("100"), volume=1000, interval="1m",
    )
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[intraday_bar])
    svc = KLineService(repo, adapter)
    await svc.get_bars(
        "X", interval="1m",
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 23, tzinfo=timezone.utc),
    )
    adapter.fetch_intraday.assert_called_once_with("X", freq="1")
    repo.insert_bars.assert_not_called()


# ---------- 4h 重采样 ----------

@pytest.mark.asyncio
async def test_get_bars_4h_groups_four_60m_bars():
    """A 股一天 4 根 60m -> 1 根 4h, 用最后一根 ts。"""
    from decimal import Decimal as D

    def _bar60(i: int, close: float) -> Bar:
        ts = datetime(2026, 5, 12, 2, 30, tzinfo=timezone.utc) + timedelta(hours=i)
        return Bar(
            market="ashare", symbol="600519.SH", ts=ts,
            open=D(str(close - 0.5)), high=D(str(close + 1.0)),
            low=D(str(close - 1.0)), close=D(str(close)),
            volume=100 + i, interval="60m",
        )

    sixty = [_bar60(i, 100.0 + i) for i in range(8)]  # 8 根 = 2 个 4h
    repo = MagicMock()
    repo.fetch_history.return_value = []  # 强制走 adapter
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=sixty)
    svc = KLineService(repo, adapter)

    bars = await svc.get_bars(
        "600519.SH", interval="4h",
        start=sixty[0].ts, end=sixty[-1].ts,
    )

    assert len(bars) == 2
    first, second = bars
    assert first.interval == "4h"
    assert first.open == D("99.5")             # bar0.open
    assert first.close == D("103.0")            # bar3.close
    assert first.high == D("104.0")             # max(bar0..bar3 high) = bar3.high
    assert first.low == D("99.0")               # bar0.low
    assert first.volume == 100 + 101 + 102 + 103
    assert first.ts == sixty[3].ts             # 末根 ts

    assert second.open == D("103.5")            # bar4.open
    assert second.close == D("107.0")
    assert second.ts == sixty[7].ts


@pytest.mark.asyncio
async def test_get_bars_4h_drops_incomplete_trailing_group():
    """不足 4 根的尾段直接丢弃, 避免半截 4h bar 污染指标计算。"""
    from decimal import Decimal as D

    def _bar60(i: int) -> Bar:
        ts = datetime(2026, 5, 12, 2, 30, tzinfo=timezone.utc) + timedelta(hours=i)
        return Bar(
            market="ashare", symbol="X", ts=ts,
            open=D("10"), high=D("11"), low=D("9"), close=D("10"),
            volume=1, interval="60m",
        )

    repo = MagicMock()
    repo.fetch_history.return_value = []
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[_bar60(i) for i in range(6)])  # 1 完整 + 2 残
    svc = KLineService(repo, adapter)

    bars = await svc.get_bars(
        "X", interval="4h",
        start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_get_bars_4h_empty_source_returns_empty():
    repo = MagicMock()
    repo.fetch_history.return_value = []
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[])
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "X", interval="4h",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 12, tzinfo=timezone.utc),
    )
    assert bars == []
