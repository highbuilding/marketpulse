"""首次回填历史 K 线到 DuckDB。

Usage:
  python -m apps.warmup --symbols 600519.SH,000858.SZ --days 365
  python -m apps.warmup --from-watchlist --days 365
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from tqdm import tqdm

from apps.api.deps import (
    get_bar_repo, get_kline_service, get_watchlist_service,
)

log = structlog.get_logger(__name__)


async def warmup(symbols: list[str], days: int) -> None:
    svc = get_kline_service()
    get_bar_repo()  # 触发 DuckDB 初始化
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    for sym in tqdm(symbols, desc="warmup"):
        try:
            bars = await svc.get_bars(sym, interval="1d", start=start, end=end)
            log.info("warmup.ok", symbol=sym, count=len(bars))
        except Exception as e:  # noqa: BLE001
            log.warning("warmup.failed", symbol=sym, error=str(e))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="逗号分隔 symbol")
    p.add_argument("--from-watchlist", action="store_true",
                    help="使用所有未归档关注列表的并集")
    p.add_argument("--days", type=int, default=365)
    args = p.parse_args()

    if args.from_watchlist:
        symbols = await get_watchlist_service().dynamic_universe()
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        p.error("must provide --symbols or --from-watchlist")
        return

    if not symbols:
        log.warning("warmup.no_symbols")
        return
    await warmup(symbols, args.days)


if __name__ == "__main__":
    asyncio.run(main())
