"""审计 B5: 15m/30m 单一来源(直取),sweep_derived 不再聚合它们。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from apps.collector.jobs import aggregate_derived as ad


@pytest.mark.asyncio
async def test_sweep_skips_15m_30m_but_keeps_60m_4h(monkeypatch):
    repo = MagicMock()
    now5 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)   # 5m/1d 较新
    old = datetime(2026, 5, 1, tzinfo=timezone.utc)          # 派生周期较旧 → 正常会触发聚合

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

    # 15m/30m 必须跳过(_NOOP);60m/4h 仍聚合
    assert captured.get("window_15m") is ad._NOOP
    assert captured.get("window_30m") is ad._NOOP
    assert captured.get("window_60m") is not ad._NOOP
    assert captured.get("window_4h") is not ad._NOOP
