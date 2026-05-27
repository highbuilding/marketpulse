from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import pandas as pd
import structlog

from core.adapters.base import MarketAdapter
from core.domain.markets import infer_market
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo
from core.services.intraday_aggregator import aggregate_intraday

log = structlog.get_logger(__name__)

Interval = Literal["1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m", "4h"]

_INTRADAY_RAW = {"1m", "5m", "15m", "30m"}  # 直拉, 不重采样
_INTRADAY_AGG = {"60m", "4h"}                # 走 aggregate_intraday (富途口径)
_RESAMPLED = {"1wk": "W-FRI", "1mo": "ME"}


class KLineService:
    def __init__(
        self, bar_repo: BarRepo,
        adapters: dict[str, MarketAdapter],
    ) -> None:
        self.repo = bar_repo
        self.adapters = adapters
        self._one_minute_ttl_s = 55
        self._one_minute_cache: dict[str, tuple[datetime, list[Bar]]] = {}
        self._one_minute_inflight: dict[str, asyncio.Task[list[Bar]]] = {}

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
        if interval in _INTRADAY_AGG:
            return await self._get_intraday_aggregated(symbol, interval, start, end)
        if interval in _RESAMPLED:
            daily = await self._get_daily(symbol, start, end)
            return _resample(daily, interval)
        if interval in _INTRADAY_RAW:
            return await self._get_intraday(symbol, interval, start, end)
        if interval == "1d":
            return await self._get_daily(symbol, start, end)
        raise ValueError(f"unsupported interval: {interval}")

    async def _get_daily(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval="1d")
        # 只有当缓存覆盖了请求窗口才命中,否则重拉
        covers = self._covers(cached, start, end) if cached else False
        metrics_missing = (
            market == "ashare"
            and cached
            and covers
            and self._missing_ashare_daily_metrics(cached)
        )
        if cached and covers and not metrics_missing:
            log.debug("kline.daily.cache_hit", symbol=symbol, market=market,
                      bars=len(cached),
                      first_ts=cached[0].ts.isoformat(),
                      last_ts=cached[-1].ts.isoformat())
            return cached
        log.info("kline.daily.cache_miss", symbol=symbol, market=market,
                 cached_bars=len(cached) if cached else 0,
                 covers=covers,
                 metrics_missing=metrics_missing,
                 req_start=start.isoformat(), req_end=end.isoformat(),
                 cached_first=cached[0].ts.isoformat() if cached else None,
                 cached_last=cached[-1].ts.isoformat() if cached else None)
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

    @staticmethod
    def _missing_ashare_daily_metrics(bars: list[Bar]) -> bool:
        recent = bars[-20:]
        return any(b.amount is None or b.turnover is None for b in recent)

    async def _get_intraday(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        if interval == "1m":
            bars = await self._get_one_minute_bars(symbol)
            return [b for b in bars if start <= b.ts <= end]
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
        if cached and self._covers(cached, start, end):
            return cached
        freq = interval.replace("m", "")
        bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq=freq)
        self.repo.insert_bars(bars)
        return [b for b in bars if start <= b.ts <= end]

    async def _get_one_minute_bars(self, symbol: str) -> list[Bar]:
        """1m 分时短缓存 + 同 symbol 请求合并。

        1m 图本身只按分钟更新；页面刷新、量能面板和用户切 tab 容易在数秒内触发多次
        相同请求。这里保留"不落库"策略，只在进程内缓存最近一次结果，避免 AKShare
        全局锁排队导致 Next 代理连接先断开。
        """
        now = datetime.now(timezone.utc)
        cached = self._one_minute_cache.get(symbol)
        if cached is not None:
            fetched_at, bars = cached
            age_s = (now - fetched_at).total_seconds()
            if age_s <= self._one_minute_ttl_s:
                log.debug("kline.1m.cache_hit", symbol=symbol, bars=len(bars), age_s=round(age_s, 1))
                return bars

        task = self._one_minute_inflight.get(symbol)
        if task is None or task.done():
            task = asyncio.create_task(self._adapter_for(symbol).fetch_intraday(symbol, freq="1"))
            self._one_minute_inflight[symbol] = task
            log.info("kline.1m.fetch_start", symbol=symbol)
        else:
            log.info("kline.1m.join_inflight", symbol=symbol)

        try:
            bars = await asyncio.shield(task)
        finally:
            if self._one_minute_inflight.get(symbol) is task and task.done():
                self._one_minute_inflight.pop(symbol, None)
        self._one_minute_cache[symbol] = (datetime.now(timezone.utc), bars)
        log.info("kline.1m.fetch_done", symbol=symbol, bars=len(bars))
        return bars

    async def _get_intraday_aggregated(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        """60m / 4h 走富途口径: 拉 5m raw → aggregate_intraday → 缓存到 DuckDB。
        ts = bar close 时刻(本市场时区 wall-clock 边界 → UTC)。
        """
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
        if cached and self._covers(cached, start, end):
            return cached
        raw = await self._adapter_for(symbol).fetch_intraday(symbol, freq="5")
        interval_minutes = 60 if interval == "60m" else 240
        agg = aggregate_intraday(raw, market, interval_minutes)
        if agg:
            self.repo.insert_bars(agg)
        return [b for b in agg if start <= b.ts <= end]

    async def get_bars_cache_only(
        self, symbol: str, *, interval: str,
        start: datetime, end: datetime,
    ) -> tuple[list[Bar], bool]:
        """只读 DuckDB cache, 不调 adapter, 不写库。

        返回 (bars, partial), partial=True 表示数据有但部分字段缺失
        (如 A 股 daily 缺 amount/turnover; 仍可绘 K 线但量能/换手缺)。

        api 进程必须用这个方法; collector 用 get_bars 或 fetch_fresh_bars。
        """
        market = infer_market(symbol)
        if interval == "1d":
            cached = self.repo.fetch_history(market, symbol, start, end, interval="1d")
            if not cached or not self._covers(cached, start, end):
                return [], False
            partial = self._missing_ashare_daily_metrics(cached) if market == "ashare" else False
            return cached, partial
        if interval in _RESAMPLED:
            # weekly/monthly resample — 复用 daily cache
            daily, partial = await self.get_bars_cache_only(
                symbol, interval="1d", start=start, end=end,
            )
            if not daily:
                return [], False
            return _resample(daily, interval), partial
        if interval in _INTRADAY_RAW or interval in _INTRADAY_AGG:
            cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
            if not cached or not self._covers(cached, start, end):
                return [], False
            return cached, False
        return [], False

    async def fetch_fresh_bars(
        self, symbol: str, *, interval: Interval,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        """绕过 cache, 强制 adapter 拉最新, 写库(UNIQUE 幂等), 返回。

        用于 SignalScanService scan cron — `_covers` 4 天 buffer 设计给 1d 用,
        intraday 不收紧会导致跨周末 / 节假日时 scan cron 短路返回 stale cache,
        新 bar 永远写不进 DB(走 scan 路径就是为了拿新数据)。

        详情页 / chart 走 `get_bars` 享受 cache 短路, scan 走这里永远 fresh。
        """
        if interval == "1d":
            bars = await self._adapter_for(symbol).fetch_history(symbol, start, end)
            self.repo.insert_bars(bars)
            return bars
        if interval in _INTRADAY_RAW and interval != "1m":
            freq = interval.replace("m", "")
            bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq=freq)
            self.repo.insert_bars(bars)
            return [b for b in bars if start <= b.ts <= end]
        if interval in _INTRADAY_AGG:
            market = infer_market(symbol)
            raw = await self._adapter_for(symbol).fetch_intraday(symbol, freq="5")
            interval_minutes = 60 if interval == "60m" else 240
            agg = aggregate_intraday(raw, market, interval_minutes)
            if agg:
                self.repo.insert_bars(agg)
            return [b for b in agg if start <= b.ts <= end]
        # 1wk / 1mo / 1m 不在 scan 路径, 回落到带 cache 的 get_bars
        return await self.get_bars(symbol, interval=interval, start=start, end=end)


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
