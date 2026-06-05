"""采集重构(2026-06-05, commit 24c0e9a): 15m/30m 改从 5m 聚合派生,
sweep_derived 放开 15m/30m(原 B5 "直取、sweep 不聚合" 设计已废)。

本测试锁住新行为: 源 5m 新于目标派生周期时, 15m/30m/60m/4h 全部触发聚合。
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from apps.collector.jobs import aggregate_derived as ad


@pytest.mark.asyncio
async def test_sweep_aggregates_all_intraday_derived(monkeypatch):
    repo = MagicMock()
    now5 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)   # 5m/1d 较新
    old = datetime(2026, 5, 1, tzinfo=timezone.utc)          # 派生周期较旧 → 触发增量聚合

    def last_map(market, iv, syms):
        return {"X": now5} if iv in ("5m", "1d") else {"X": old}

    def first_map(market, iv, syms):
        return {"X": old}

    repo.fetch_last_ts_map.side_effect = last_map
    repo.fetch_first_ts_map.side_effect = first_map

    captured: dict = {}

    async def fake_agg(repo, market, symbol, **kw):
        captured.update(kw)
        return {}

    monkeypatch.setattr(ad, "aggregate_derived_for_symbol", fake_agg)
    await ad.sweep_derived(repo, "ashare", ["X"])

    # 15m/30m 现在也从 5m 聚合(放开); 60m/4h 一直聚合。全部非 _NOOP。
    assert captured.get("window_15m") is not ad._NOOP
    assert captured.get("window_30m") is not ad._NOOP
    assert captured.get("window_60m") is not ad._NOOP
    assert captured.get("window_4h") is not ad._NOOP
