"""一次性: 删旧信号 + 用现有 DuckDB bar 重算写新(与 scan_symbol_readonly 同口径)。

修正两类历史问题:
- close/open 对齐偏移(原 cron scan 走 fetch_fresh_bars 现聚合 60m/4h, close 对齐)
- 历史回填缺口(scan 约 5 月才起步, 1~4 月 bar 未扫)

做法: 对三市场全 CORE 标的全信号周期, 直读 DuckDB bar → compute_cd_signals
     → 先删该 (symbol,interval) 旧信号 → 写新(ts 口径统一为库内 bar 口径)。

跑前已备份 data/state.db。Usage: python -m apps.rescan_all_signals
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

import duckdb

from core.domain.core_symbols import CORE_SYMBOLS
from core.domain.models import Bar, IndicatorSignal
from core.indicators.cd import compute_cd_signals
from core.persistence.signal_repo import SignalRepo

IVS = ["15m", "30m", "60m", "4h", "1d"]
STATE_DB = "data/state.db"
BARS_DB = {
    "ashare": "data/bars_ashare.duckdb",
    "us": "data/bars_us.duckdb",
    "crypto": "data/bars_crypto.duckdb",
}


def load_bars(market: str, symbol: str, interval: str) -> list[Bar]:
    con = duckdb.connect(BARS_DB[market], read_only=True)
    try:
        rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM bars "
            "WHERE symbol=? AND interval=? ORDER BY ts",
            [symbol, interval],
        ).fetchall()
    finally:
        con.close()
    out: list[Bar] = []
    for r in rows:
        ts = r[0]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(Bar(market=market, symbol=symbol, ts=ts,
                       open=r[1], high=r[2], low=r[3], close=r[4],
                       volume=int(r[5] or 0), interval=interval))
    return out


async def main() -> None:
    repo = SignalRepo(STATE_DB)
    raw = sqlite3.connect(STATE_DB)
    total_deleted = total_written = 0
    for market in ("ashare", "us", "crypto"):
        for sym in CORE_SYMBOLS[market]:
            for iv in IVS:
                bars = load_bars(market, sym, iv)
                if not bars:
                    continue
                cds = compute_cd_signals(bars)
                # 先删该 (symbol,interval) 旧信号(清除偏移残留), 再写新
                cur = raw.execute(
                    "DELETE FROM indicator_signals WHERE symbol=? AND interval=?",
                    [sym, iv],
                )
                deleted = cur.rowcount
                raw.commit()
                det = datetime.now(timezone.utc)
                recs = [
                    IndicatorSignal(
                        symbol=sym, interval=iv, indicator="CD",
                        signal_type=s.signal_type, bar_ts=s.bar_ts,
                        detected_at=det, price=s.price, d_value=s.d_value,
                    )
                    for s in cds
                ]
                n = await repo.upsert_many(recs)
                total_deleted += deleted
                total_written += n
                print(f"{market:7} {sym:10} {iv:4} bars={len(bars):6} "
                      f"删={deleted:4} 写={n:4}")
    raw.close()
    print(f"\n总计: 删 {total_deleted} 写 {total_written}")


if __name__ == "__main__":
    asyncio.run(main())
