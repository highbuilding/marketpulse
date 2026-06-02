"""审计补漏: tick_snapshot 标的集并入 CORE(首页默认股 quote 不依赖 watchlist)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from core.scheduler.jobs import tick_snapshot_once


@pytest.mark.asyncio
async def test_tick_snapshot_includes_core_symbols(monkeypatch):
    monkeypatch.setattr("core.domain.market_calendar.is_trading_day", lambda m: True)
    monkeypatch.setattr("core.domain.market_sessions.is_market_session_open", lambda m: True)
    captured = {}
    adapter = MagicMock()

    async def fake_snapshot(symbols):
        captured["symbols"] = set(symbols)
        return []

    adapter.fetch_snapshot = fake_snapshot
    registry = MagicMock()
    registry.get.return_value = adapter
    registry.universe.return_value = []
    registry.index_symbols.return_value = []
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=[])
    cache = MagicMock()
    await tick_snapshot_once("ashare", registry, cache, wl, redis_cache=None)
    # 首页默认股(CORE 内、非 watchlist)应在抓取集合
    assert "300059.SZ" in captured["symbols"]
    assert "002594.SZ" in captured["symbols"]
    assert "600519.SH" in captured["symbols"]
