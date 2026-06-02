from unittest.mock import AsyncMock, MagicMock

import pytest

from core.scheduler.signal_jobs import scan_cd_job


@pytest.mark.asyncio
async def test_scan_cd_job_invokes_scan_many_with_universe():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["600519.SH", "300750.SZ"])
    svc = MagicMock()
    svc.scan_many = AsyncMock(return_value=3)
    await scan_cd_job(svc, wl, None, interval="1d")
    # 并入 core_symbols 后列表更大,只验证 watchlist 标的都在其中
    passed = set(svc.scan_many.await_args[0][0])
    assert {"600519.SH", "300750.SZ"}.issubset(passed)
    assert svc.scan_many.await_args[0][1] == "1d"
    assert svc.scan_many.await_args[1]["market_filter"] is None


@pytest.mark.asyncio
async def test_scan_cd_job_skips_when_watchlist_empty():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=[])
    svc = MagicMock()
    svc.scan_many = AsyncMock()
    await scan_cd_job(svc, wl, None, interval="1d")
    svc.scan_many.assert_not_called()


@pytest.mark.asyncio
async def test_scan_cd_job_passes_market_filter():
    """显式传入的 market_filter 必须透传给 scan_many,且 watchlist 标的仍在集合内。"""
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["AAPL", "600519.SH"])
    svc = MagicMock()
    svc.scan_many = AsyncMock(return_value=0)
    await scan_cd_job(svc, wl, None, interval="1d", market_filter="ashare")
    # 并入 core_symbols 后列表更大,验证 market_filter 透传 + watchlist 标的仍在
    passed = set(svc.scan_many.await_args[0][0])
    assert {"AAPL", "600519.SH"}.issubset(passed)
    assert svc.scan_many.await_args[0][1] == "1d"
    assert svc.scan_many.await_args[1]["market_filter"] == "ashare"


@pytest.mark.asyncio
async def test_scan_cd_job_invokes_notify_after_scan():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["600519.SH"])
    svc = MagicMock()
    svc.scan_many = AsyncMock(return_value=1)
    notify = MagicMock()
    notify.maybe_send_summary = AsyncMock(return_value=True)
    await scan_cd_job(svc, wl, notify, interval="1d", market_filter="ashare")
    notify.maybe_send_summary.assert_awaited_once_with("ashare")


@pytest.mark.asyncio
async def test_scan_cd_job_notify_failure_does_not_propagate():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["600519.SH"])
    svc = MagicMock()
    svc.scan_many = AsyncMock(return_value=1)
    notify = MagicMock()
    notify.maybe_send_summary = AsyncMock(side_effect=RuntimeError("smtp died"))
    # 不应抛
    await scan_cd_job(svc, wl, notify, interval="1d", market_filter="ashare")
