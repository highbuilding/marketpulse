"""美股分时图写入器。逐笔 VWAP 累加 (TradeHub 驱动), 仅 RTH 写分时点。

均价线 = 当日累计成交额/累计成交量 (真 VWAP, Σp×s / Σs)。
独立分时库 intraday_us.duckdb (物理隔离, 雷区 6)。成交量为 IEX 口径。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.domain.market_sessions import is_us_regular_session
from core.persistence.intraday_repo import IntradayLineRepo, IntradayPoint

log = structlog.get_logger(__name__)


def compute_us_point(symbol: str, ts: datetime, *, price: float,
                     cum_amount: float, cum_volume: int) -> IntradayPoint:
    """算分时点。均价 = 累计成交额/累计成交量; 量为 0 退化为现价。ts 截断到分钟。"""
    avg = (cum_amount / cum_volume) if cum_volume else price
    minute_ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return IntradayPoint(symbol=symbol, ts=minute_ts, price=price,
                         cum_amount=cum_amount, cum_volume=cum_volume, avg_price=avg)


class UsIntradayWriter:
    """美股分时 writer。无 loop, 由 TradeHub 节流调 flush。"""

    def __init__(self, repo: IntradayLineRepo, redis):
        self._repo = repo
        self._redis = redis

    async def flush(self, symbol: str, accum, *, now: datetime) -> None:
        if not is_us_regular_session(now):
            return  # 仅 RTH 画分时
        if accum.cum_volume <= 0 and accum.last_price <= 0:
            return
        p = compute_us_point(symbol, now, price=accum.last_price,
                             cum_amount=accum.cum_amount, cum_volume=accum.cum_volume)
        try:
            self._repo.insert_points([p])
        except Exception as e:  # noqa: BLE001
            log.warning("us_intraday.write_failed", symbol=symbol, error=str(e))
            return
        payload = {"market": "us", "symbol": symbol, "ts": p.ts.isoformat(),
                   "price": p.price, "avg_price": p.avg_price}
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_INTRADAY_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=20000, approximate=True)
            await self._redis.set_msgpack(
                keys.cache_intraday_current("us", symbol), payload, ttl_s=120)
        except Exception as e:  # noqa: BLE001
            log.warning("us_intraday.publish_failed", symbol=symbol, error=str(e))
