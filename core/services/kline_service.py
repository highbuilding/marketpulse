from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import pandas as pd
import structlog

from core.adapters.base import MarketAdapter
from core.domain.markets import infer_market
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

Interval = Literal["1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m", "4h"]

_INTRADAY = {"1m", "5m", "15m", "30m", "60m"}
_RESAMPLED = {"1wk": "W-FRI", "1mo": "ME"}
_FOUR_HOUR_GROUP_BY_MARKET: dict[str, int] = {
    "us":     4,  # 美股 prepost 16 根 60m / 天 → 4 根 4h
    "crypto": 4,  # crypto 24h 连续, 6 根 4h / 天
    "ashare": 4,  # A 股 4 根 60m / 天 → 4h ≡ 1d, 通常不展示
    "hk":     4,  # 同上
}


class KLineService:
    def __init__(
        self, bar_repo: BarRepo,
        adapters: dict[str, MarketAdapter],
    ) -> None:
        self.repo = bar_repo
        self.adapters = adapters

    def _adapter_for(self, symbol: str) -> MarketAdapter:
        m = infer_market(symbol)
        a = self.adapters.get(m)
        if a is None:
            raise ValueError(f"no adapter for market={m} (symbol={symbol})")
        return a

    async def get_bars(
        self, symbol: str, *, interval: Interval,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        if interval == "4h":
            market = infer_market(symbol)
            group_size = _FOUR_HOUR_GROUP_BY_MARKET.get(market, 4)
            sixty = await self._get_intraday(symbol, "60m", start, end)
            return _group_resample(sixty, group_size, "4h")
        if interval in _RESAMPLED:
            daily = await self._get_daily(symbol, start, end)
            return _resample(daily, interval)
        if interval in _INTRADAY:
            return await self._get_intraday(symbol, interval, start, end)
        if interval == "1d":
            return await self._get_daily(symbol, start, end)
        raise ValueError(f"unsupported interval: {interval}")

    async def _get_daily(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval="1d")
        # 只有当缓存覆盖了请求窗口才命中,否则重拉
        if cached and self._covers(cached, start, end):
            return cached
        bars = await self._adapter_for(symbol).fetch_history(symbol, start, end)
        self.repo.insert_bars(bars)
        return bars

    @staticmethod
    def _covers(bars: list[Bar], start: datetime, end: datetime) -> bool:
        """缓存是否真正覆盖 [start, end] 请求窗口。

        判断标准:**缓存末点足够新**,且**缓存起点要么比 start 早,要么很接近 start**。
        - tail_ok: last >= end - 4 天 (容许周末 + 1 个假期)
        - head_ok: first <= start + 1 天 (允许首交易日 vs start 差 1 天)

        例外:**当 cache span 已经"足够长"(>= 请求窗口的 80%)**,即使 first 比 start 晚也算覆盖
        (这处理 IPO 标的:start=2020 但股票 2023 才上市)。
        """
        if not bars:
            return False
        first = bars[0].ts
        last = bars[-1].ts
        from datetime import timedelta
        tail_ok = last >= end - timedelta(days=4)
        if not tail_ok:
            return False
        head_close_enough = first <= start + timedelta(days=1)
        if head_close_enough:
            return True
        # IPO 例外:cache span 覆盖了请求窗口的 80% 以上
        req_span = (end - start).total_seconds()
        cache_span = (last - first).total_seconds()
        return req_span > 0 and cache_span / req_span >= 0.8

    async def _get_intraday(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        # 1m 不缓存,总是拿最新
        if interval == "1m":
            bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq="1")
            return [b for b in bars if start <= b.ts <= end]
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
        if cached and self._covers(cached, start, end):
            return cached
        freq = interval.replace("m", "")
        bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq=freq)
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


def _group_resample(source: list[Bar], group_size: int, target_interval: str) -> list[Bar]:
    """每 group_size 根聚成 1 根。
    用于 4h(A 股 60m × 4 根/天 = 1 天 1 根 4h)。
    时间戳取每组最后一根的 ts, 与"收盘时点"对齐, 与日线/富途惯例一致。
    """
    if not source:
        return []
    out: list[Bar] = []
    sample = source[0]
    for i in range(0, len(source) - group_size + 1, group_size):
        chunk = source[i:i + group_size]
        out.append(Bar(
            market=sample.market, symbol=sample.symbol,
            ts=chunk[-1].ts,
            open=chunk[0].open,
            high=max(b.high for b in chunk),
            low=min(b.low for b in chunk),
            close=chunk[-1].close,
            volume=sum(b.volume for b in chunk),
            interval=target_interval,
        ))
    return out
