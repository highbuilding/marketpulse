"""Task 2: scan_cd_job / fetch_intraday_job 标的集并入 core_symbols。"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from core.scheduler.signal_jobs import scan_cd_job, fetch_intraday_job


@pytest.mark.asyncio
async def test_scan_cd_job_unions_core_symbols(monkeypatch):
    monkeypatch.setattr("core.domain.market_calendar.is_trading_day", lambda m: True)
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=["ZZZZ"])
    scan = MagicMock(); scan.scan_many = AsyncMock(return_value=0)
    await scan_cd_job(scan, wl, None, interval="1d", market_filter="us")
    passed = set(scan.scan_many.await_args[0][0])
    assert "AAPL" in passed and "QQQ" in passed   # core 并入
    assert "ZZZZ" in passed                          # watchlist 保留


@pytest.mark.asyncio
async def test_fetch_intraday_job_unions_core(monkeypatch):
    monkeypatch.setattr("core.domain.market_calendar.is_trading_day", lambda m: True)
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=[])
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    await fetch_intraday_job(kline, wl, interval="5m", market_filter="us")
    called_syms = {c.args[0] for c in kline.fetch_fresh_bars.await_args_list}
    assert "AAPL" in called_syms   # core 标的被采 5m
