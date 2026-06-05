import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from apps.collector.startup_reconcile import run_startup_reconcile

NOW = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def _mk_kline(*, tf_result=None, tf_exc=None):
    """构造 kline mock: fetch_fresh_bars + _adapter_for(sym).fetch_history_tf。"""
    kline = MagicMock()
    kline.fetch_fresh_bars = AsyncMock()
    adapter = MagicMock()
    if tf_exc is not None:
        adapter.fetch_history_tf = AsyncMock(side_effect=tf_exc)
    else:
        adapter.fetch_history_tf = AsyncMock(return_value=tf_result or [])
    kline._adapter_for = MagicMock(return_value=adapter)
    return kline, adapter


@pytest.mark.asyncio
async def test_skips_when_data_current(monkeypatch):
    """warm restart: 数据新鲜 → 不拉外部、不聚合、不直拉(避免 burst 打爆熔断器)。"""
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {"AAPL": NOW - timedelta(minutes=5)}
    kline, adapter = _mk_kline()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    kline.fetch_fresh_bars.assert_not_awaited()
    adapter.fetch_history_tf.assert_not_awaited()
    agg.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_direct_pull_all_intervals_no_agg(monkeypatch):
    """空库种子: 1d + 5m/15m/30m + 60m/4h/1wk/1mo 全源头直拉成功 → 不聚合。"""
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    # 直拉返回非空(成功)
    kline, adapter = _mk_kline(tf_result=[MagicMock()])
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    # 1d + 直取分钟走 fetch_fresh_bars
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals[0] == "1d"
    assert set(intervals) >= {"1d", "5m", "15m", "30m"}
    # 60m/4h/1wk/1mo 走 adapter 直拉
    tf_ivs = {c.args[1] for c in adapter.fetch_history_tf.await_args_list}
    assert tf_ivs == {"60m", "4h", "1wk", "1mo"}
    # 全直拉成功 → 不聚合
    agg.assert_not_awaited()
    repo.insert_bars.assert_called()


@pytest.mark.asyncio
async def test_seed_agg_fallback_only_for_week_month(monkeypatch):
    """直拉失败: 仅 1wk/1mo 走聚合兜底; 60m/4h 拿不到就跳过(不聚合)。"""
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    # 所有 fetch_history_tf 都抛(A股月线/4h em不稳 / 收盘后 60m 崩 的场景)
    kline, adapter = _mk_kline(tf_exc=NotImplementedError("no direct"))
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("ashare", repo, kline, ["600519.SH"], now=NOW)
    # 聚合兜底只针对周/月
    agg.assert_awaited_once()
    kw = agg.await_args.kwargs
    assert "window_1wk" in kw and "window_1mo" in kw
    assert "window_60m" not in kw and "window_4h" not in kw  # 60m/4h 不兜底


@pytest.mark.asyncio
async def test_fetches_1d_only_when_intraday_current_but_daily_stale(monkeypatch):
    """日线陈旧但 intraday/tf 都新鲜: 只补 1d, 不重拉其他。"""
    repo = MagicMock()

    def last_map(m, iv, syms):
        if iv == "1d":
            return {"AAPL": NOW - timedelta(days=10)}
        return {"AAPL": NOW - timedelta(hours=1)}

    repo.fetch_last_ts_map.side_effect = last_map
    kline, adapter = _mk_kline()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", AsyncMock())
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals == ["1d"]
    adapter.fetch_history_tf.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_symbol_failure_does_not_abort(monkeypatch):
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    kline, adapter = _mk_kline()
    kline.fetch_fresh_bars = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", AsyncMock())
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    # 不抛: 单标的失败被吞
    await run_startup_reconcile("us", repo, kline, ["AAPL", "MSFT"], now=NOW)
