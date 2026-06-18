"""A 股 collector 离线命令行 — 事实层回填/维护。

⚠️ 需 collector **停机**时跑(读 bars DuckDB, 与运行中 collector 的 RW 连接冲突——雷区6)。

用法:
  python -m apps.collector.ashare.cli backfill-limit-pool [--from 2024-01-01] [--to 2026-06-18]

子命令:
  backfill-limit-pool   从日线 OHLC 反推涨停生态历史(涨停/炸板/跌停/连板/昨板), 落 limit_pool_daily。
                        EM 涨停池只服务最近~3周, 故长历史靠价格反推。仅覆盖已采集标的(宽度偏低)。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[3]
_DATA = _BASE / "data"


async def _backfill_limit_pool(date_from: str, date_to: str, market: str = "ashare") -> None:
    from core.persistence.collector_symbol_repo import CollectorSymbolRepo
    from core.persistence.duckdb_repo import BarRepo
    from core.persistence.limit_pool_repo import LimitPoolRepo
    from core.services.limit_reconstruct_service import reconstruct_from_bars

    start = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)

    csr = CollectorSymbolRepo(str(_DATA / "state.db"))
    symbols = sorted(set(await csr.active_symbols(market, capability="signals")))
    if not symbols:
        print("无 signals 采集标的, 退出")
        return
    print(f"标的 {len(symbols)} 只 | 区间 {date_from} .. {date_to}")

    bar_repo = BarRepo(str(_DATA / f"bars_{market}.duckdb"))
    try:
        bar_repo.init()
        frame = bar_repo.fetch_history_frame(market, symbols, start, end, "1d")
    except Exception as e:  # noqa: BLE001
        print(f"读 DuckDB 失败(collector 是否在运行?请先停 collector 再跑): {e}")
        sys.exit(1)
    if frame is None or frame.empty:
        print("日线为空, 无可反推数据")
        return

    items = reconstruct_from_bars(frame, market=market)
    repo = LimitPoolRepo(str(_DATA / "state.db"))
    affected = await repo.save_items(items)

    by_type = Counter(it.pool_type for it in items)
    days = len({it.trade_date for it in items})
    print(f"反推完成: 生成 {len(items)} 行 {dict(by_type)}; 覆盖 {days} 个交易日; upsert 影响 {affected} 行")


def main() -> None:
    p = argparse.ArgumentParser(
        prog="apps.collector.ashare.cli", description="A 股事实层离线命令(collector 停机时跑)")
    sub = p.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("backfill-limit-pool", help="日线反推涨停生态历史 → limit_pool_daily")
    bp.add_argument("--from", dest="date_from", default="2024-01-01", help="起始交易日 YYYY-MM-DD")
    bp.add_argument("--to", dest="date_to", default=datetime.now().strftime("%Y-%m-%d"),
                    help="结束交易日 YYYY-MM-DD(默认今天)")

    args = p.parse_args()
    if args.cmd == "backfill-limit-pool":
        asyncio.run(_backfill_limit_pool(args.date_from, args.date_to))


if __name__ == "__main__":
    main()
