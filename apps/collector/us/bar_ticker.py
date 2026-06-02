"""美股 K 线进行中态 (trades 驱动)。

逐笔攒进行中桶, 推 final=false + 写 :current (不入库)。
桶滚动时对刚收桶补发 provisional final=true 仅到 bus (填 REST SIP ~20min 延迟洞,
不写 DuckDB —— DuckDB 只存 SIP 权威收线 bar)。tracker 由 TradeHub 维护。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import structlog

from core.cache import keys
from core.domain.bucket_state import BucketState

log = structlog.get_logger(__name__)

# 进行中态支持的周期 → 分钟
INTERVAL_MIN = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "4h": 240}


@dataclass
class BucketTracker:
    open_ts: datetime
    close_ts: datetime
    state: BucketState


def _payload(symbol: str, interval: str, tr: BucketTracker, *, final: bool) -> dict:
    return {
        "market": "us", "symbol": symbol, "interval": interval,
        "ts": tr.close_ts.isoformat(),
        "open": float(tr.state.open), "high": float(tr.state.high),
        "low": float(tr.state.low), "close": float(tr.state.close),
        "volume": int(tr.state.volume), "final": final,
    }


class UsBarTicker:
    def __init__(self, redis):
        self._redis = redis

    async def publish_current(self, symbol: str, interval: str, tr: BucketTracker) -> None:
        """进行中桶: final=false + 写 :current。"""
        mins = INTERVAL_MIN.get(interval, 5)
        payload = _payload(symbol, interval, tr, final=False)
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True)
            await self._redis.set_msgpack(
                keys.cache_bars_current("us", symbol, interval), payload, ttl_s=mins * 120)
        except Exception as e:  # noqa: BLE001
            log.warning("us_ticker.publish_failed",
                        symbol=symbol, interval=interval, error=str(e))

    async def publish_provisional(self, symbol: str, interval: str, tr: BucketTracker) -> None:
        """桶滚动: 刚收桶补发 final=true 仅到 bus (填 SIP 洞, 不入库不写 :current)。"""
        payload = _payload(symbol, interval, tr, final=True)
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True)
        except Exception as e:  # noqa: BLE001
            log.warning("us_ticker.provisional_failed",
                        symbol=symbol, interval=interval, error=str(e))
