from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import pandas as pd
import structlog

from core.adapters.base import MarketAdapter
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

Interval = Literal["1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m"]

_INTRADAY = {"1m", "5m", "15m", "30m", "60m"}
_RESAMPLED = {"1wk": "W-FRI", "1mo": "ME"}


class KLineService:
    def __init__(self, bar_repo: BarRepo, adapter: MarketAdapter) -> None:
        self.repo = bar_repo
        self.adapter = adapter

    async def get_bars(
        self, symbol: str, *, interval: Interval,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        if interval in _RESAMPLED:
            daily = await self._get_daily(symbol, start, end)
            return _resample(daily, interval)
        if interval in _INTRADAY:
            return await self._get_intraday(symbol, interval, start, end)
        if interval == "1d":
            return await self._get_daily(symbol, start, end)
        raise ValueError(f"unsupported interval: {interval}")

    async def _get_daily(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        cached = self.repo.fetch_history("ashare", symbol, start, end, interval="1d")
        if cached:
            return cached
        bars = await self.adapter.fetch_history(symbol, start, end)
        self.repo.insert_bars(bars)
        return bars

    async def _get_intraday(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        # 1m 分时不缓存:总是拿最新;其他 intraday 走 cache
        if interval != "1m":
            cached = self.repo.fetch_history("ashare", symbol, start, end, interval=interval)
            if cached:
                return cached
        freq = interval.replace("m", "")
        bars = await self.adapter.fetch_intraday(symbol, freq=freq)
        if interval != "1m":
            self.repo.insert_bars(bars)
        return [b for b in bars if start <= b.ts <= end]


def _resample(daily: list[Bar], interval: str) -> list[Bar]:
    if not daily:
        return []
    df = pd.DataFrame([{
        "ts": b.ts, "open": float(b.open), "high": float(b.high),
        "low": float(b.low), "close": float(b.close), "volume": b.volume,
    } for b in daily]).set_index("ts")
    rule = _RESAMPLED[interval]
    agg = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    sample = daily[0]
    return [Bar(
        market=sample.market, symbol=sample.symbol,
        ts=ts.to_pydatetime().replace(tzinfo=timezone.utc),
        open=Decimal(str(r["open"])), high=Decimal(str(r["high"])),
        low=Decimal(str(r["low"])), close=Decimal(str(r["close"])),
        volume=int(r["volume"]), interval=interval,
    ) for ts, r in agg.iterrows()]
