"""美股批量回填: 558 标的 × 4 基础周期 (1d/5m/15m/30m).

策略:
- 1d: pre-2020 回填 (2010-01-01 ~ 2020-01-01), 约 10 年日线
- 5m/15m/30m: 拉近期 (adapter 默认窗口, Alpaca free ~60 天)
- 串行执行, 单标的失败不阻塞整批
- 数据写入 bars_us.duckdb
- 美股走 Alpaca (primary) → yfinance (backup), 不依赖 Redis

用法:
    NO_PROXY='*' . .venv/bin/activate && python scratch/backfill_us.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from core.integrations.proxy_setup import setup_process_proxy

setup_process_proxy()

from core.integrations.logging_setup import setup_logging

setup_logging(process_name="us_backfill")

import structlog

from core.adapters.us import USAdapter
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

DATA = Path(__file__).resolve().parents[1] / "data"
SYMBOLS_FILE = DATA / "us_backfill_symbols.txt"

# pre-2020 日线窗口
DAILY_START = datetime(2010, 1, 1, tzinfo=timezone.utc)
DAILY_END = datetime(2020, 1, 1, tzinfo=timezone.utc)

INTRADAY_FREQS = [("5m", "5"), ("15m", "15"), ("30m", "30")]


def load_symbols() -> list[str]:
    if not SYMBOLS_FILE.exists():
        print(f"符号文件不存在: {SYMBOLS_FILE}")
        return []
    with open(SYMBOLS_FILE) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


async def backfill_one_symbol(
    adapter: USAdapter,
    repo: BarRepo,
    symbol: str,
    idx: int,
    total: int,
) -> dict:
    """回填单标的全周期, 返回统计."""
    stats = {"symbol": symbol, "1d": 0, "5m": 0, "15m": 0, "30m": 0, "errors": []}

    # 1) 日线 pre-2020
    try:
        bars = await adapter.fetch_history(symbol, DAILY_START, DAILY_END)
        if bars:
            repo.insert_bars(bars)
            stats["1d"] = len(bars)
    except Exception as e:
        stats["errors"].append(f"1d:{e}")
        log.warning("backfill_us.1d_failed", symbol=symbol, error=str(e))

    # 2) 日内周期 (近期)
    for iv, freq in INTRADAY_FREQS:
        try:
            bars = await adapter.fetch_intraday(symbol, freq=freq)
            if bars:
                repo.insert_bars(bars)
                stats[iv] = len(bars)
        except Exception as e:
            stats["errors"].append(f"{iv}:{e}")
            log.warning("backfill_us.intraday_failed", symbol=symbol, interval=iv, error=str(e))

    total_bars = stats["1d"] + stats["5m"] + stats["15m"] + stats["30m"]
    print(f"  [{idx}/{total}] {symbol}: 1d={stats['1d']} 5m={stats['5m']} "
          f"15m={stats['15m']} 30m={stats['30m']} | errors={len(stats['errors'])}")
    return stats


async def main() -> None:
    symbols = load_symbols()
    if not symbols:
        print("没有找到回填标的")
        return

    print(f"美股回填: {len(symbols)} 只标的")
    print(f"  1d 窗口: {DAILY_START.date()} ~ {DAILY_END.date()}")
    print(f"  日内: 5m/15m/30m (adapter 默认近期窗口)")
    print(f"  adapter: Alpaca (primary) → yfinance (backup)")
    print()

    repo = BarRepo(str(DATA / "bars_us.duckdb"))
    repo.init()
    adapter = USAdapter()
    if adapter.has_primary:
        print("  Alpaca keys detected, will use Alpaca as primary")
    else:
        print("  No Alpaca keys, using yfinance fallback only")

    total_bars = 0
    total_errors = 0
    completed = 0

    for i, sym in enumerate(symbols, 1):
        stats = await backfill_one_symbol(adapter, repo, sym, i, len(symbols))
        total_bars += stats["1d"] + stats["5m"] + stats["15m"] + stats["30m"]
        total_errors += len(stats["errors"])
        if stats["1d"] > 0 or stats["5m"] > 0:
            completed += 1
        # Alpaca free tier: 200 req/min → 每 0.35s 一次安全
        await asyncio.sleep(0.35)

    print(f"\n完成: {completed}/{len(symbols)} 标的有数据, "
          f"总计 {total_bars} 根 bar, {total_errors} 个错误")


if __name__ == "__main__":
    asyncio.run(main())
