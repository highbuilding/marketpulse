"""一次性补救:sina qfq 当日因子未入表 / 缓存 _covers 太宽松,导致
- intraday(5/15/30/60m)当日 OHLC 全 NaN 被过滤,DuckDB 缺当日数据
- 1d 缓存末点 = end-4d 时 tail_ok 通过, _covers 命中, 不重抓最近交易日

本脚本对 watchlist 所有标的:
1) bypass _covers 缓存检查,直接走 adapter.fetch_intraday(已含 NaN 兜底)+ insert_bars
2) bypass _covers,直接走 adapter.fetch_history(近 30 天)+ insert_bars 补 1d
3) 重扫所有 SIGNAL_INTERVALS 的 CD 信号

Usage: python -m apps.repair_intraday_today
注意:跑前需停 API 进程(DuckDB 单写者)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from apps.api.deps import (
    get_bar_repo, get_kline_service, get_signal_scan_service,
    get_watchlist_service,
)
from core.domain.intervals import SIGNAL_INTERVALS

log = structlog.get_logger(__name__)

INTRADAY_FREQS = ["5m", "15m", "30m", "60m"]
DAILY_LOOKBACK_DAYS = 30


async def repair() -> None:
    kline = get_kline_service()
    bar_repo = get_bar_repo()
    scan = get_signal_scan_service()
    wl = get_watchlist_service()

    symbols = await wl.dynamic_universe()
    log.info("repair.start", symbols=symbols)
    if not symbols:
        log.warning("repair.no_symbols")
        return

    # step 1: bypass _covers, 强制重抓 intraday + 入库(insert_bars 是 upsert,幂等)
    for sym in symbols:
        for iv in INTRADAY_FREQS:
            try:
                freq = iv.replace("m", "")
                bars = await kline.adapter.fetch_intraday(sym, freq=freq)
                bar_repo.insert_bars(bars)
                log.info("repair.refilled", symbol=sym, interval=iv,
                         count=len(bars))
            except Exception as e:  # noqa: BLE001
                log.warning("repair.refill_failed", symbol=sym, interval=iv,
                            error=str(e))

    # step 2: bypass _covers, 强制重抓 1d 近 30 天 + 入库
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_LOOKBACK_DAYS)
    for sym in symbols:
        try:
            bars = await kline.adapter.fetch_history(sym, start, end)
            bar_repo.insert_bars(bars)
            log.info("repair.refilled", symbol=sym, interval="1d",
                     count=len(bars))
        except Exception as e:  # noqa: BLE001
            log.warning("repair.refill_failed", symbol=sym, interval="1d",
                        error=str(e))

    # step 3: 重扫信号(SignalRepo upsert_many 幂等,只新增不重复)
    for sym in symbols:
        for iv in SIGNAL_INTERVALS:
            try:
                n = await scan.scan_symbol(sym, iv)
                log.info("repair.scanned", symbol=sym, interval=iv, new=n)
            except Exception as e:  # noqa: BLE001
                log.warning("repair.scan_failed", symbol=sym, interval=iv,
                            error=str(e))

    log.info("repair.done")


if __name__ == "__main__":
    asyncio.run(repair())
