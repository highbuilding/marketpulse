import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from apps.collector.startup_reconcile import run_startup_reconcile

NOW = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_skips_when_data_current(monkeypatch):
    """warm restart: 数据新鲜 → 不拉外部、不聚合(避免 burst 打爆熔断器)。"""
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {"AAPL": NOW - timedelta(minutes=5)}
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    kline.fetch_fresh_bars.assert_not_awaited()
    agg.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetches_when_missing_1d_first_then_intraday_then_agg(monkeypatch):
    """缺口(全缺): 先 1d → 再 5m/15m/30m → 最后聚合派生。"""
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals[0] == "1d"
    assert set(intervals) >= {"1d", "5m", "15m", "30m"}
    agg.assert_awaited()


@pytest.mark.asyncio
async def test_fetches_1d_only_when_intraday_current_but_daily_stale(monkeypatch):
    """日线陈旧但 intraday 新鲜: 只补 1d, 不重拉 intraday(live poller 自愈近端)。"""
    repo = MagicMock()

    def last_map(m, iv, syms):
        if iv == "1d":
            return {"AAPL": NOW - timedelta(days=10)}
        return {"AAPL": NOW - timedelta(hours=1)}

    repo.fetch_last_ts_map.side_effect = last_map
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", AsyncMock())
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals == ["1d"]


@pytest.mark.asyncio
async def test_one_symbol_failure_does_not_abort(monkeypatch):
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", AsyncMock())
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL", "MSFT"], now=NOW)
