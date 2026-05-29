"""crypto 全周期历史回填.

启动时一次性跑(全周期 5 标的, 能拉多少拉多少),之后每天 04:00 UTC 兜底再拉.

设计:
- 串行 5 × 8 = 40 个 (symbol, interval) 回填,每个之间 sleep 0.2s 避免 Binance 限频
- 各 interval 历史窗口长度按 INTERVAL_LOOKBACK 控制 (5m 拉 30 天, 1d 拉 8 年)
  避免 5m 拉 2017 至今 ~88 万根的浪费
- 写 DuckDB BarRepo + Redis tail (供 api 进程读)
- 单条失败不阻塞整批 (优雅降级)

参考: docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from core.adapters.binance import BinanceAdapter
from core.cache.redis_bars_cache import RedisBarsCache
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

INTERVALS = ("5m", "15m", "30m", "60m", "4h", "1d", "1wk", "1mo")
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT")

# 各 interval 回填窗口 (天). 大周期拉历史尽头, 小周期只拉近期足量
INTERVAL_LOOKBACK_DAYS: dict[str, int] = {
    "5m": 30,        # ~8640 bars
    "15m": 60,       # ~5760
    "30m": 120,      # ~5760
    "60m": 365,      # ~8760
    "4h": 365 * 3,   # ~6570
    "1d": 365 * 12,  # 拉到 BTC 上市起 ~3000+
    "1wk": 365 * 12, # ~459
    "1mo": 365 * 12, # ~106
}


def _start_for(interval: str, end: datetime) -> datetime:
    days = INTERVAL_LOOKBACK_DAYS.get(interval, 30)
    return end - timedelta(days=days)


async def backfill_one(
    adapter: BinanceAdapter,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
    symbol: str,
    interval: str,
) -> None:
    end = datetime.now(timezone.utc)
    start = _start_for(interval, end)
    try:
        bars = await adapter.fetch_klines(symbol, interval, start, end)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "crypto.backfill_failed", symbol=symbol, interval=interval, error=str(e)
        )
        return
    if not bars:
        log.info("crypto.backfill_empty", symbol=symbol, interval=interval)
        return
    try:
        repo.insert_bars(bars)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "crypto.backfill_db_write_failed",
            symbol=symbol,
            interval=interval,
            error=str(e),
        )
    try:
        await redis_bars.upsert_tail("crypto", symbol, interval, bars)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "crypto.backfill_redis_write_failed",
            symbol=symbol,
            interval=interval,
            error=str(e),
        )
    log.info(
        "crypto.backfill_done", symbol=symbol, interval=interval, bars=len(bars)
    )


async def run_backfill(
    adapter: BinanceAdapter,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
) -> None:
    """串行跑 5 × 8 = 40 个 (symbol, interval) 回填。"""
    log.info("crypto.backfill_start", symbols=len(SYMBOLS), intervals=len(INTERVALS))
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            await backfill_one(adapter, repo, redis_bars, symbol, interval)
            await asyncio.sleep(0.2)  # 限流缓冲
    log.info("crypto.backfill_all_done")

