"""A 股分时图(时分线)写入器。quote 驱动, 每 10s 写当日逐分钟分时点。

读最新 quote → 算 price + 累计成交额 + 累计成交量 + 均价 → upsert 独立分时库
+ 发 bus:intraday.updated + 写 :current 缓存。只在交易时段写。
标的集来自 collector_symbols, 不依赖 SSE subscription。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.intraday_repo import IntradayLineRepo, IntradayPoint

log = structlog.get_logger(__name__)
WRITE_INTERVAL_S = 10


def compute_point(symbol: str, ts: datetime, *, price: float,
                  cum_amount: float, cum_volume: int) -> IntradayPoint:
    """算分时点。均价 = 累计成交额/累计成交量; 量为 0 时退化为现价。ts 截断到分钟。"""
    avg = (cum_amount / cum_volume) if cum_volume else price
    minute_ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return IntradayPoint(symbol=symbol, ts=minute_ts, price=price,
                         cum_amount=cum_amount, cum_volume=cum_volume, avg_price=avg)


class IntradayLineWriter:
    def __init__(
        self,
        repo: IntradayLineRepo,
        redis,
        collector_symbols: CollectorSymbolRepo | None = None,
    ):
        self._repo = repo
        self._redis = redis
        self._collector_symbols = collector_symbols
        self._stopped = False

    async def write_once(self, symbol: str, *, now: datetime) -> None:
        try:
            q = await self._redis.get_msgpack(keys.cache_quote("ashare", symbol))
        except Exception:  # noqa: BLE001
            q = None
        if not q:
            return
        price = float(q.get("price"))
        cum_volume = int(q.get("volume") or 0)
        cum_amount = float(q.get("amount") or 0.0)
        p = compute_point(symbol, now, price=price,
                          cum_amount=cum_amount, cum_volume=cum_volume)
        try:
            self._repo.insert_points([p])
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.write_failed", symbol=symbol, error=str(e))
            return
        payload = {"market": "ashare", "symbol": symbol, "ts": p.ts.isoformat(),
                   "price": p.price, "avg_price": p.avg_price}
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_INTRADAY_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=20000, approximate=True)
            await self._redis.set_msgpack(
                keys.cache_intraday_current("ashare", symbol), payload, ttl_s=120)
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.publish_failed", symbol=symbol, error=str(e))

    async def _scan_symbols(self) -> set[str]:
        if self._collector_symbols is None:
            return set()
        try:
            return set(await self._collector_symbols.active_symbols(
                "ashare", capability="snapshot"))
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.symbol_scan_failed", error=str(e))
            return set()

    async def run(self) -> None:
        log.info("intraday_line_writer.started")
        while not self._stopped:
            try:
                now = datetime.now(timezone.utc)
                if is_trading_day("ashare") and is_market_session_open("ashare", now):
                    for symbol in await self._scan_symbols():
                        await self.write_once(symbol, now=now)
            except Exception as e:  # noqa: BLE001
                log.warning("intraday.loop_error", error=str(e))
            await asyncio.sleep(WRITE_INTERVAL_S)


async def run_intraday_line_writer(
    repo: IntradayLineRepo,
    redis,
    collector_symbols: CollectorSymbolRepo,
) -> None:
    await IntradayLineWriter(repo, redis, collector_symbols).run()
