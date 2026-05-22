"""CD 信号扫描 job: 对关注列表所有标的扫一次某周期的 CD 信号。"""
from __future__ import annotations

import structlog

from core.services.notification_service import NotificationService
from core.services.signal_service import SignalScanService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def scan_cd_job(
    signal_scan: SignalScanService,
    watchlist: WatchlistService,
    notify_service: NotificationService | None,
    *, interval: str, market_filter: str | None = None,
) -> None:
    symbols = await watchlist.dynamic_universe()
    if not symbols:
        log.debug("cd.scan_skipped_empty_watchlist", interval=interval,
                  market_filter=market_filter)
        return
    n = await signal_scan.scan_many(
        symbols, interval, market_filter=market_filter,
    )
    log.info("cd.scan_done", interval=interval,
             market_filter=market_filter, symbols=len(symbols), new=n)
    # 扫完一轮 → snapshot 比对 → 必要时发通知。失败不污染 scan job 状态
    if market_filter and notify_service is not None:
        try:
            await notify_service.maybe_send_summary(market_filter)
        except Exception as e:  # noqa: BLE001
            log.warning("notify.maybe_send_failed",
                        market=market_filter, error=str(e))
