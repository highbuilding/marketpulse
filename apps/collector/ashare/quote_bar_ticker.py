"""A 股 K 线进行中态组件 (quote 驱动).

每 10s 读最新 quote, 对每个被 SSE 订阅的 (symbol, interval), 维护当前未收线桶的
进行中 bar (high/low/close 随 quote 跳), 推 final=false + 写 :current, 不入库。
收线由源头采集 (bar_poller) 负责。对齐 crypto 的进行中态体验。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from core.cache import keys
from core.domain.bucket_state import BucketState, current_bucket, seed_baseline, update_bucket
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open

log = structlog.get_logger(__name__)

_INTERVAL_MIN = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "4h": 240}
TICK_INTERVAL_S = 10


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
        if prev is None or prev[0] != open_ts:
            # 新桶: 用已收线 5m bar 补 OHLC 基线(大周期才需要)
            base = None
            if self._repo is not None and mins > 5:
                try:
                    src = self._repo.fetch_history_paged(
                        "ashare", symbol, "5m", before=close_ts, limit=mins // 5)
                    src = [b for b in src if open_ts < b.ts <= close_ts]
                    base = seed_baseline(src)
                except Exception:  # noqa: BLE001
                    base = None
            state = base
        else:
            state = prev[1]
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
                payload, ttl_s=mins * 120,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ticker.publish_failed",
                        symbol=symbol, interval=interval, error=str(e))

    async def _scan_subscribed(self) -> set[tuple[str, str]]:
        active: set[tuple[str, str]] = set()
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:ashare:*", count=200)
                for k in found:
                    key = k.decode() if isinstance(k, bytes) else k
                    parts = key.split(":")
                    if len(parts) >= 5 and parts[4] in _INTERVAL_MIN:
                        active.add((parts[3], parts[4]))
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("ticker.scan_failed", error=str(e))
        return active

    async def run(self) -> None:
        log.info("quote_bar_ticker.started")
        while not self._stopped:
            try:
                if is_trading_day("ashare") and is_market_session_open("ashare"):
                    now = datetime.now(timezone.utc)
                    for symbol, interval in await self._scan_subscribed():
                        await self.tick_once(symbol, interval, now=now)
            except Exception as e:  # noqa: BLE001
                log.warning("ticker.loop_error", error=str(e))
            await asyncio.sleep(TICK_INTERVAL_S)


async def run_quote_bar_ticker(redis, repo) -> None:
    await QuoteBarTicker(redis, repo).run()
