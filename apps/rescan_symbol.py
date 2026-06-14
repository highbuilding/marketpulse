"""一次性重扫单个 symbol 的 CD 信号(零 DuckDB 写锁冲突)。

路径:collector 只读 HTTP 拉已存 bar → compute_cd_signals(纯函数)
     → SignalRepo.upsert_many(SQLite, 幂等)。
不调 fetch_fresh_bars(那个写 DuckDB,会撞 collector RW 锁,雷区 6)。

Usage: python -m apps.rescan_symbol PDD
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import httpx
import structlog

from apps.api.deps import get_signal_repo
from core.domain.markets import infer_market
from core.domain.models import Bar
from core.indicators.cd import compute_cd_signals
from core.domain.models import IndicatorSignal

log = structlog.get_logger(__name__)

# 美股 collector 内嵌只读接口
PORT = {"us": 8789, "ashare": 8788, "crypto": 8790}
SIGNAL_INTERVALS = ["1d", "4h", "60m", "30m", "15m"]


async def _fetch_bars(port: int, symbol: str, interval: str, limit: int = 600) -> list[Bar]:
    url = f"http://127.0.0.1:{port}/internal/bars/history"
    async with httpx.AsyncClient(trust_env=False, timeout=20) as c:
        r = await c.get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
        r.raise_for_status()
        data = r.json()
    mkt = infer_market(symbol)
    out: list[Bar] = []
    for b in data.get("bars", []):
        out.append(Bar(
            market=mkt, symbol=symbol, ts=datetime.fromisoformat(b["ts"]),
            open=b["open"], high=b["high"], low=b["low"], close=b["close"],
            volume=b.get("volume") or 0, interval=interval, amount=b.get("amount"),
        ))
    return out


async def rescan(symbol: str) -> None:
    mkt = infer_market(symbol)
    port = PORT[mkt]
    repo = get_signal_repo()
    for iv in SIGNAL_INTERVALS:
        try:
            bars = await _fetch_bars(port, symbol, iv)
            if not bars:
                log.warning("rescan.no_bars", symbol=symbol, interval=iv)
                continue
            cds = compute_cd_signals(bars)
            detected_at = datetime.now(timezone.utc)
            records = [
                IndicatorSignal(
                    symbol=symbol, interval=iv, indicator="CD",
                    signal_type=s.signal_type, bar_ts=s.bar_ts,
                    detected_at=detected_at, price=s.price, d_value=s.d_value,
                )
                for s in cds
            ]
            n = await repo.upsert_many(records)
            log.info("rescan.done", symbol=symbol, interval=iv,
                     bars=len(bars), signals=len(cds), new=n,
                     latest_bar=str(bars[-1].ts))
        except Exception as e:  # noqa: BLE001
            log.warning("rescan.failed", symbol=symbol, interval=iv, error=str(e))


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "PDD"
    asyncio.run(rescan(sym))
