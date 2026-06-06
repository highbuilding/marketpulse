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
async def test_skips_fetch_when_data_current(monkeypatch):
    """warm restart: 数据新鲜 → 不拉外部、不直拉(避免 burst 打爆熔断器)。

    注: 派生聚合是本地 CPU + 幂等 upsert, 新逻辑每标的都跑一次(源为空则 noop),
    不算外部调用, 故此处只断言"不拉外部"。
    """
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {"AAPL": NOW - timedelta(minutes=5)}
    kline, adapter = _mk_kline()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    kline.fetch_fresh_bars.assert_not_awaited()
    adapter.fetch_history_tf.assert_not_awaited()


@pytest.mark.asyncio
async def test_ashare_seed_only_5m_1d_direct_rest_aggregated(monkeypatch):
    """A股空库种子: 仅 5m + 1d 源头直取; 不走 TF 直拉; 末尾全量聚合派生周期。"""
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    kline, adapter = _mk_kline()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("ashare", repo, kline, ["600519.SH"], now=NOW)
    # 直取只有 1d + 5m
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals[0] == "1d"
    assert set(intervals) == {"1d", "5m"}
    # A股 direct_tf 为空 → 不走 adapter.fetch_history_tf
    adapter.fetch_history_tf.assert_not_awaited()
    # 末尾全量聚合: 15m/30m/60m/4h ← 5m, 1wk/1mo ← 1d
    agg.assert_awaited_once()
    kw = agg.await_args.kwargs
    assert all(kw[f"window_{iv}"] is None
               for iv in ("15m", "30m", "60m", "4h", "1wk", "1mo"))


@pytest.mark.asyncio
async def test_us_seed_only_5m_1d_direct_rest_aggregated(monkeypatch):
    """美股空库种子(2026-06-06 回退后 = 与 A股统一): 仅 5m+1d 直取; 不走 TF 直拉;
    末尾全量聚合 15m/30m/60m/4h ← 5m, 1wk/1mo ← 1d(富途口径单锚点)。

    回退原因: Alpaca 60m/4h 整点切桶, 无视美股 09:30 开盘(4h 盘前盘中混一根),
    锚点不符看盘习惯且与聚合双写 → 改为从 5m 聚合。
    """
    repo = MagicMock()
    repo.fetch_last_ts_map.side_effect = lambda m, iv, syms: {}
    kline, adapter = _mk_kline()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"], now=NOW)
    # 直取只有 1d + 5m
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals[0] == "1d"
    assert set(intervals) == {"1d", "5m"}
    # 美股 direct_tf 现为空 → 不走 adapter.fetch_history_tf(与 A股一致)
    adapter.fetch_history_tf.assert_not_awaited()
    # 末尾全量聚合: 15m/30m/60m/4h ← 5m, 1wk/1mo ← 1d
    agg.assert_awaited_once()
    kw = agg.await_args.kwargs
    assert all(kw[f"window_{iv}"] is None
               for iv in ("15m", "30m", "60m", "4h", "1wk", "1mo"))


@pytest.mark.asyncio
async def test_fetches_1d_only_when_intraday_current_but_daily_stale(monkeypatch):
    """日线陈旧但 5m/TF 都新鲜: 只补 1d, 不重拉分钟/TF(聚合仍跑, 幂等)。"""
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
