"""CD 信号扫描 job: 对关注列表所有标的扫一次某周期的 CD 信号。"""
from __future__ import annotations

import structlog

from core.services.signal_service import SignalScanService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def scan_cd_job(
    signal_scan: SignalScanService,
    watchlist: WatchlistService,
    *, interval: str,
) -> None:
    symbols = await watchlist.dynamic_universe()
    if not symbols:
        log.debug("cd.scan_skipped_empty_watchlist", interval=interval)
        return
    n = await signal_scan.scan_many(symbols, interval)
    log.info("cd.scan_done", interval=interval, symbols=len(symbols), new=n)
