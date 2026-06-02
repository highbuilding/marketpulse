"""启动 reconcile: collector 开机对核心+watchlist 标的回补缺口。

冷启动填历史、kill 后补断档(各市场 adapter 窗口内)。先 1d 再聚合派生
(1wk/1mo 依赖 1d 先到位)。失败单标的 try/except, 不阻塞启动。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol

log = structlog.get_logger(__name__)

_DIRECT_INTRADAY = ("5m", "15m", "30m")
_DAILY_LOOKBACK_DAYS = 1000
THROTTLE_S = 0.3


async def run_startup_reconcile(market: str, repo, kline, symbols: list[str]) -> None:
    """collector 开机回补入口。

    Args:
        market:  市场标识 ("ashare" / "us" / "crypto")
        repo:    BarRepo 实例(传给 aggregate_derived_for_symbol)
        kline:   KLineService 实例(提供 fetch_fresh_bars)
        symbols: 待回补标的列表(核心 + watchlist)
    """
    log.info("startup_reconcile.start", market=market, symbols=len(symbols))
    now = datetime.now(timezone.utc)
    filled = 0
    for sym in symbols:
        try:
            # 1. 补日线(长窗口)
            await kline.fetch_fresh_bars(
                sym,
                interval="1d",
                start=now - timedelta(days=_DAILY_LOOKBACK_DAYS),
                end=now,
            )
            # 2. 补直取 intraday(短窗口)
            for iv in _DIRECT_INTRADAY:
                await kline.fetch_fresh_bars(
                    sym,
                    interval=iv,
                    start=now - timedelta(days=60),
                    end=now,
                )
            # 3. 重聚合派生(60m/4h 从 5m, 1wk/1mo 从 1d)
            await aggregate_derived_for_symbol(
                repo, market, sym,
                window_60m=None,
                window_4h=None,
                window_1wk=None,
                window_1mo=None,
            )
            filled += 1
        except Exception as e:  # noqa: BLE001
            log.warning(
                "startup_reconcile.symbol_failed",
                market=market,
                symbol=sym,
                error=str(e),
            )
        await asyncio.sleep(THROTTLE_S)
    log.info("startup_reconcile.done", market=market, filled=filled, total=len(symbols))
