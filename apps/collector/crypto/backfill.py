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
from datetime import datetime, timezone

import structlog

from core.adapters.binance import BinanceAdapter
from core.cache.redis_bars_cache import RedisBarsCache
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

INTERVALS = ("5m", "15m", "30m", "60m", "4h", "1d", "1wk", "1mo")
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT")

# 回填起点固定为 Binance 上线前 (2017-07-01 UTC). 所有 interval 从此拉到 now,
# 让 Binance 自然返回该交易对上市起的全部历史 (用户要求"完整的, 不受 20 年后限制").
# adapter 反向分页只传 endTime, Binance 返回最早一页即停, 不会真拉到 2017 之前的空数据.
# 量级参考 (BTC, 单标的): 5m ≈ 84 万根, 15m ≈ 28 万, 1d ≈ 3000+, 1mo ≈ 100.
# 写库走 DuckDB DataFrame 批量 upsert (77 万行 ~3s); Redis tail 仍截 MAX 2000.
BINANCE_GENESIS = datetime(2017, 7, 1, tzinfo=timezone.utc)


def _start_for(interval: str, end: datetime) -> datetime:  # noqa: ARG001
    return BINANCE_GENESIS


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

