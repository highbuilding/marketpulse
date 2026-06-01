"""A 股 K 线进行中态组件 (quote 驱动).

每 10s 读最新 quote, 对每个被 SSE 订阅的 (symbol, interval), 维护当前未收线桶的
进行中 bar (high/low/close 随 quote 跳), 推 final=false + 写 :current, 不入库。
收线由源头采集 (bar_poller) 负责。对齐 crypto 的进行中态体验。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import structlog

from core.cache import keys
from core.domain.market_sessions import bucket_grid

log = structlog.get_logger(__name__)

_INTERVAL_MIN = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "4h": 240}
TICK_INTERVAL_S = 10


@dataclass
class BucketState:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def update_bucket(state: BucketState | None, price: Decimal, *, volume: int) -> BucketState:
    """用新 quote 价更新进行中桶 OHLC。

    state=None 时新建桶 (open=high=low=close=price)。
    已有 state 时: open 保持, high=max, low=min, close=price, volume 取累计最新值。
    """
    if state is None:
        return BucketState(open=price, high=price, low=price, close=price, volume=volume)
    return BucketState(
        open=state.open,
        high=max(state.high, price),
        low=min(state.low, price),
        close=price,
        volume=volume,
    )


def current_bucket(
    market: str, now: datetime, interval_min: int,
) -> tuple[datetime, datetime] | None:
    """返回 now 落在的桶 (open_utc, close_utc]; 不在任何桶(休市)返回 None。"""
    grid = bucket_grid(market, now.date(), interval_min)
    for open_utc, close_utc in grid:
        if open_utc <= now < close_utc:
            return (open_utc, close_utc)
    return None


class QuoteBarTicker:
    """quote 驱动所有被订阅周期的进行中 bar (final=false), 不入库。"""

    def __init__(self, redis, repo=None):
        self._redis = redis
        self._repo = repo
        self._buckets: dict[str, tuple[datetime, BucketState]] = {}  # key=sym:iv
        self._stopped = False

    async def tick_once(self, symbol: str, interval: str, *, now: datetime) -> None:
        mins = _INTERVAL_MIN.get(interval)
        if mins is None:
            return
        ob = current_bucket("ashare", now, mins)
        if ob is None:
            return  # 休市
        open_ts, close_ts = ob
        try:
            q = await self._redis.get_msgpack(keys.cache_quote("ashare", symbol))
        except Exception:  # noqa: BLE001
            q = None
        if not q:
            return
        price = Decimal(str(q.get("price")))
        volume = int(q.get("volume") or 0)
        tk = f"{symbol}:{interval}"
        prev = self._buckets.get(tk)
        # 新桶则从头攒(基线补全在 Task 10 接入, 这里先 None)
        state = None if (prev is None or prev[0] != open_ts) else prev[1]
        state = update_bucket(state, price, volume=volume)
        self._buckets[tk] = (open_ts, state)
        payload = {
            "market": "ashare", "symbol": symbol, "interval": interval,
            "ts": close_ts.isoformat(),
            "open": float(state.open), "high": float(state.high),
            "low": float(state.low), "close": float(state.close),
            "volume": int(state.volume), "final": False,
        }
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True,
            )
            await self._redis.set_msgpack(
                keys.cache_bars_current("ashare", symbol, interval),
                payload, ttl=mins * 120,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ticker.publish_failed",
                        symbol=symbol, interval=interval, error=str(e))
