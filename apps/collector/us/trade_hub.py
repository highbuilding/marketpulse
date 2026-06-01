"""美股逐笔成交实时中枢 (替代 1m bar 频道)。

Alpaca IEX WS `trades` 逐笔 → TradeAccumulator 累加当日 RTH VWAP +
TradeHub 维护进行中桶 + ~1s 节流分发给分时 writer / K 线 ticker。
1m 不落库。收线由 UsBarPoller (REST SIP) 负责。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import structlog

log = structlog.get_logger(__name__)

_ET = ZoneInfo("America/New_York")
FLUSH_INTERVAL_S = 1.0
SUBS_REFRESH_TICKS = 5  # 每 5 个 flush tick (~5s) 刷新订阅


def _et_date(ts: datetime) -> date:
    return ts.astimezone(_ET).date()


class TradeAccumulator:
    """单标的当日 RTH 累计成交额/量, 跨 ET 日自动重置。VWAP = cum_amount/cum_volume。"""

    def __init__(self) -> None:
        self.session_date: date | None = None
        self.cum_amount: float = 0.0
        self.cum_volume: int = 0
        self.last_price: float = 0.0

    def _maybe_reset(self, ts: datetime) -> None:
        d = _et_date(ts)
        if self.session_date != d:
            self.session_date = d
            self.cum_amount = 0.0
            self.cum_volume = 0

    def add_trade(self, *, price: float, size: int, ts: datetime) -> None:
        self._maybe_reset(ts)
        self.cum_amount += price * size
        self.cum_volume += size
        self.last_price = price

    def vwap(self) -> float:
        return (self.cum_amount / self.cum_volume) if self.cum_volume else 0.0
