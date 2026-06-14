"""核对: crypto 各标的 5m~1d 的 CD 信号, 数据库已存 vs 现在重算 是否完全匹配。
重算用 collector 只读分页接口拉 2026-01-01 以来全部 bar → compute_cd_signals。
不写库, 纯只读对比。
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import httpx
from core.domain.models import Bar
from core.persistence.signal_repo import SignalRepo

PORT = 8790  # crypto collector
SYMS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT"]
IVS = ["15m", "30m", "60m", "4h", "1d"]
SINCE = "2026-01-01T00:00:00+00:00"
DB = "data/state.db"


async def fetch_all_bars(client, symbol, interval):
    """游标分页往前翻, 拉全 >= SINCE 的 bar(升序)。"""
    out = {}
    before = None
    while True:
        params = {"symbol": symbol, "interval": interval, "limit": 2000}
        if before:
            params["before"] = before
        r = await client.get(f"http://127.0.0.1:{PORT}/internal/bars/history", params=params)
        bars = r.json().get("bars", [])
        if not bars:
            break
        for b in bars:
            out[b["ts"]] = b
        earliest = bars[0]["ts"]
        if earliest < SINCE or len(bars) < 2000:
            break
        before = earliest
    # 转 Bar, 只留 >= SINCE
    res = []
    for ts in sorted(out):
        if ts < SINCE:
            continue
        b = out[ts]
        res.append(Bar(market="crypto", symbol=symbol, ts=datetime.fromisoformat(b["ts"]),
                       open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                       volume=b.get("volume") or 0, interval=interval, amount=b.get("amount")))
    return res


async def main():
    from core.indicators.cd import compute_cd_signals
    repo = SignalRepo(DB)
    total_db = total_recompute = total_match = total_only_db = total_only_recompute = 0
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        for sym in SYMS:
            for iv in IVS:
                bars = await fetch_all_bars(client, sym, iv)
                recomputed = {(s.bar_ts.isoformat(), s.signal_type) for s in compute_cd_signals(bars)
                              if s.bar_ts.isoformat() >= SINCE}
                # DB 已存
                sigs = await repo.list_by_symbol(sym, intervals=[iv], limit=10000)
                db = {(s.bar_ts.isoformat(), s.signal_type) for s in sigs
                      if s.bar_ts.isoformat() >= SINCE}
                match = db & recomputed
                only_db = db - recomputed
                only_re = recomputed - db
                total_db += len(db); total_recompute += len(recomputed)
                total_match += len(match); total_only_db += len(only_db); total_only_recompute += len(only_re)
                flag = "✅" if not only_db and not only_re else "❌"
                print(f"{flag} {sym:9} {iv:4} | bars={len(bars):5} DB={len(db):3} 重算={len(recomputed):3} "
                      f"匹配={len(match):3} 仅DB={len(only_db)} 仅重算={len(only_re)}")
                if only_db:
                    print(f"     仅DB(库里有/重算无): {sorted(only_db)[:5]}")
                if only_re:
                    print(f"     仅重算(应有/库里缺): {sorted(only_re)[:5]}")
    print(f"\n总计: DB={total_db} 重算={total_recompute} 匹配={total_match} "
          f"仅DB={total_only_db} 仅重算={total_only_recompute}")
    print("结论:", "完全匹配 ✅" if total_only_db == 0 and total_only_recompute == 0 else "存在差异 ❌")


if __name__ == "__main__":
    asyncio.run(main())
