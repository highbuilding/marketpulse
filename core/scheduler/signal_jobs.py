"""CD 信号扫描 job: 对关注列表所有标的扫一次某周期的 CD 信号。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from core.domain.intervals import BARS_PER_DAY, LOOKBACK_BARS
from core.domain.markets import infer_market
from core.services.kline_service import KLineService
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


async def fetch_intraday_job(
    kline: KLineService, watchlist: WatchlistService,
    *, interval: str, market_filter: str,
) -> None:
    """只入库 raw intraday(不扫信号)。用于 5m 这种 is_signal=False 但需要
    K 线展示的周期, 让 watchlist 内标的 5m 桶能跟得上 (scan cron 不覆盖它)。
    """
    symbols = await watchlist.dynamic_universe()
    symbols = [s for s in symbols if infer_market(s) == market_filter]
    if not symbols:
        log.debug("fetch.skip_empty", interval=interval, market_filter=market_filter)
        return
    end = datetime.now(timezone.utc)
    # 拉过去 7 天就够覆盖周末 + 几个交易日, 不需要 LOOKBACK_BARS 那么深
    days = max(LOOKBACK_BARS.get(interval, 0) // BARS_PER_DAY.get(interval, 1), 7)
    start = end - timedelta(days=days)
    ok = 0
    for sym in symbols:
        try:
            await kline.fetch_fresh_bars(sym, interval=interval, start=start, end=end)
            ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("fetch.intraday_failed",
                        symbol=sym, interval=interval, error=str(e))
    log.info("fetch.intraday_done", interval=interval,
             market_filter=market_filter, ok=ok, total=len(symbols))

