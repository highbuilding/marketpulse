"""A 股 bar_poller 采集集单测。

采集集 = CORE ∪ watchlist (core_symbols.py 注释定义, 与 reconcile/settlement 一致)。
根因(2026-06-09): bar_poller 曾只采 CORE, 漏 watchlist → watchlist 非 CORE 标的
(如 603986.SH)盘中无 5m K线, 现价(tick含watchlist)与K线(poller只CORE)不一致。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.collector.ashare.bar_poller import BarPoller
from core.domain.core_symbols import CORE_SYMBOLS


def _poller(watchlist):
    return BarPoller(MagicMock(), MagicMock(), MagicMock(), watchlist=watchlist)


@pytest.mark.asyncio
async def test_scan_subscriptions_is_core_when_no_watchlist():
    poller = _poller(None)
    active = await poller._scan_subscriptions()
    assert active == {f"{s}:5m" for s in CORE_SYMBOLS["ashare"]}


@pytest.mark.asyncio
async def test_scan_subscriptions_includes_watchlist():
    # watchlist 含一个非 CORE 的 A 股标的 → 必须纳入采集集
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["603986.SH", "AAPL"])  # AAPL 非A股, 过滤掉
    poller = _poller(wl)
    active = await poller._scan_subscriptions()
    assert "603986.SH:5m" in active             # watchlist A股标的纳入
    assert "AAPL:5m" not in active               # 非A股过滤
    # CORE 仍在
    for s in CORE_SYMBOLS["ashare"]:
        assert f"{s}:5m" in active


@pytest.mark.asyncio
async def test_scan_subscriptions_watchlist_failure_degrades_to_core():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(side_effect=RuntimeError("db down"))
    poller = _poller(wl)
    active = await poller._scan_subscriptions()
    # watchlist 加载失败 → 优雅降级到 CORE, 不抛
    assert active == {f"{s}:5m" for s in CORE_SYMBOLS["ashare"]}
