from unittest.mock import AsyncMock, MagicMock

import pytest

from core.scheduler.signal_jobs import scan_cd_job


@pytest.mark.asyncio
async def test_scan_cd_job_invokes_scan_many_with_universe():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=["600519.SH", "300750.SZ"])
    svc = MagicMock()
    svc.scan_many = AsyncMock(return_value=3)
    await scan_cd_job(svc, wl, interval="1d")
    svc.scan_many.assert_awaited_once_with(["600519.SH", "300750.SZ"], "1d")


@pytest.mark.asyncio
async def test_scan_cd_job_skips_when_watchlist_empty():
    wl = MagicMock()
    wl.dynamic_universe = AsyncMock(return_value=[])
    svc = MagicMock()
    svc.scan_many = AsyncMock()
    await scan_cd_job(svc, wl, interval="1d")
    svc.scan_many.assert_not_called()
