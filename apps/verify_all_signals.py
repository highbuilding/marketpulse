"""三市场 CD 信号核对: 数据库已存信号 vs 用 DuckDB 现存 bar 重算, 是否匹配。
- 直接 read_only 连各市场 bars DuckDB 拿 bar(零写锁: 只读)。
- 重算用 compute_cd_signals, 与 indicator_signals 表按 (bar_ts, signal_type) diff。
- 对齐口径: bar 在 DuckDB 里已是各市场标准口径(crypto=open, A股/美股=close);
  信号重算与写库走同一 compute_cd_signals, 故 bar_ts 应一致。差异即真实不一致。
只读, 不写库。
"""
from __future__ import annotations
import asyncio
import sqlite3
from datetime import datetime, timezone
import duckdb
from core.domain.models import Bar
from core.indicators.cd import compute_cd_signals
from core.domain.core_symbols import CORE_SYMBOLS

SINCE = "2026-01-01T00:00:00+00:00"
IVS = ["15m", "30m", "60m", "4h", "1d"]
STATE_DB = "data/state.db"
BARS_DB = {"ashare": "data/bars_ashare.duckdb", "us": "data/bars_us.duckdb",
           "crypto": "data/bars_crypto.duckdb"}


def load_bars(market, symbol, interval):
    con = duckdb.connect(BARS_DB[market], read_only=True)
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE symbol=? AND interval=? AND ts >= TIMESTAMP '2026-01-01 00:00:00' ORDER BY ts",
        [symbol, interval],
    ).fetchall()
    con.close()
    return [Bar(market=market, symbol=symbol,
                ts=r[0].replace(tzinfo=timezone.utc) if r[0].tzinfo is None else r[0],
                open=r[1], high=r[2], low=r[3], close=r[4], volume=int(r[5] or 0),
                interval=interval) for r in rows]


def main():
    state = sqlite3.connect(STATE_DB)
    grand = {"db": 0, "re": 0, "match": 0, "only_db": 0, "only_re": 0}
    for market in ("ashare", "us", "crypto"):
        print(f"\n========== {market} ==========")
        for sym in CORE_SYMBOLS[market]:
            for iv in IVS:
                bars = load_bars(market, sym, iv)
                if not bars:
                    continue
                recomp = {(s.bar_ts.isoformat(), s.signal_type)
                          for s in compute_cd_signals(bars)
                          if s.bar_ts.isoformat() >= SINCE}
                dbset = {(r[0] if 'T' in r[0] else r[0].replace(' ', 'T'), r[1])
                         for r in state.execute(
                    "SELECT bar_ts, signal_type FROM indicator_signals "
                    "WHERE symbol=? AND interval=? AND bar_ts>=?", [sym, iv, SINCE])}
                # 规范化 db 端 bar_ts 到带 +00:00
                dbset = {((ts if ts.endswith('+00:00') else ts + '+00:00'), t) for ts, t in dbset}
                match = dbset & recomp
                only_db = dbset - recomp
                only_re = recomp - dbset
                grand["db"] += len(dbset); grand["re"] += len(recomp)
                grand["match"] += len(match); grand["only_db"] += len(only_db); grand["only_re"] += len(only_re)
                if only_db or only_re:
                    print(f"  ❌ {sym:10} {iv:4} DB={len(dbset):3} 重算={len(recomp):3} "
                          f"匹配={len(match):3} 仅DB={len(only_db)} 仅重算={len(only_re)}")
                    if only_db:
                        print(f"       仅DB: {sorted(only_db)[:3]}")
                    if only_re:
                        print(f"       仅重算: {sorted(only_re)[:3]}")
    print(f"\n===== 总计 =====")
    print(f"DB={grand['db']} 重算={grand['re']} 匹配={grand['match']} "
          f"仅DB={grand['only_db']} 仅重算={grand['only_re']}")


if __name__ == "__main__":
    main()
