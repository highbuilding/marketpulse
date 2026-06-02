import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.collector.startup_reconcile import run_startup_reconcile


@pytest.mark.asyncio
async def test_reconcile_fetches_1d_first_then_intraday_then_aggregates(monkeypatch):
    repo = MagicMock()
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"])
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals[0] == "1d"                      # 1d 先
    assert set(intervals) >= {"1d", "5m", "15m", "30m"}
    agg.assert_awaited()                              # 之后聚合派生


@pytest.mark.asyncio
async def test_reconcile_one_symbol_failure_does_not_abort(monkeypatch):
    repo = MagicMock()
    kline = MagicMock()
    kline.fetch_fresh_bars = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", AsyncMock())
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL", "MSFT"])  # 不抛出
