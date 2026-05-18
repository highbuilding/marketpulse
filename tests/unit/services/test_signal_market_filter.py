from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.services.signal_service import SignalScanService


@pytest.mark.asyncio
async def test_scan_many_filters_by_market():
    """market_filter='us' 时, scan_symbol 只对 us 标的调用。"""
    kline = MagicMock()
    repo = MagicMock()
    svc = SignalScanService(kline, repo)
    svc.scan_symbol = AsyncMock(return_value=1)
    universe = ["AAPL", "600519.SH", "9988.HK", "BTC/USDT", "SPY"]
    await svc.scan_many(universe, "60m", market_filter="us")
    called_syms = [c.args[0] for c in svc.scan_symbol.call_args_list]
    assert set(called_syms) == {"AAPL", "SPY"}


@pytest.mark.asyncio
async def test_scan_many_no_filter_keeps_all():
    """无 market_filter 时, 全部 symbol 都喂给 scan_symbol。"""
    kline = MagicMock()
    repo = MagicMock()
    svc = SignalScanService(kline, repo)
    svc.scan_symbol = AsyncMock(return_value=0)
    universe = ["AAPL", "600519.SH"]
    await svc.scan_many(universe, "60m")
    called_syms = [c.args[0] for c in svc.scan_symbol.call_args_list]
    assert set(called_syms) == {"AAPL", "600519.SH"}


@pytest.mark.asyncio
async def test_scan_many_filter_ashare():
    """market_filter='ashare' 只过滤 A 股标的(防止 US cron 跑到 A 股头上反过来同理)。"""
    kline = MagicMock()
    repo = MagicMock()
    svc = SignalScanService(kline, repo)
    svc.scan_symbol = AsyncMock(return_value=0)
    universe = ["AAPL", "600519.SH", "000001.SZ", "9988.HK"]
    await svc.scan_many(universe, "1d", market_filter="ashare")
    called_syms = [c.args[0] for c in svc.scan_symbol.call_args_list]
    assert set(called_syms) == {"600519.SH", "000001.SZ"}
