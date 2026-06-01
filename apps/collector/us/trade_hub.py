"""美股逐笔成交实时中枢 (替代 1m bar 频道)。

Alpaca IEX WS `trades` 逐笔 → TradeAccumulator 累加当日 RTH VWAP +
TradeHub 维护进行中桶 + ~1s 节流分发给分时 writer / K 线 ticker。
1m 不落库。收线由 UsBarPoller (REST SIP) 负责。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog

from core.domain.bucket_state import BucketState, current_bucket, seed_baseline, update_bucket
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
from apps.collector.us.bar_ticker import INTERVAL_MIN, BucketTracker

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


class TradeHub:
    """逐笔中枢: 累加器 + 进行中桶维护 + ~1s 节流分发。"""

    def __init__(self, redis, repo, writer, ticker):
        self._redis = redis
        self._repo = repo          # bar_repo (RW, 同进程查已收 5m 补基线)
        self._writer = writer      # UsIntradayWriter
        self._ticker = ticker      # UsBarTicker
        self._accums: dict[str, TradeAccumulator] = {}
        self._buckets: dict[tuple[str, str], BucketTracker] = {}
        self._just_closed: dict[tuple[str, str], BucketTracker] = {}
        self._subs: dict[str, set[str]] = {}   # symbol -> {interval} (订阅)
        self._dirty: set[str] = set()
        self._stopped = False

    def on_trade(self, symbol: str, *, price: float, size: int, ts: datetime) -> None:
        """逐笔处理 (同步纯内存)。累加 + 更新各订阅周期当前桶 + 滚动检测。"""
        acc = self._accums.get(symbol)
        if acc is None:
            acc = self._accums[symbol] = TradeAccumulator()
        acc.add_trade(price=price, size=size, ts=ts)

        price_dec = Decimal(str(price))
        for interval in self._subs.get(symbol, set()):
            mins = INTERVAL_MIN.get(interval)
            if mins is None:
                continue
            ob = current_bucket("us", ts, mins)
            if ob is None:
                continue
            open_ts, close_ts = ob
            key = (symbol, interval)
            tr = self._buckets.get(key)
            if tr is None or tr.open_ts != open_ts:
                if tr is not None:
                    self._just_closed[key] = tr   # 旧桶滚动 → provisional 待发
                base = self._seed(symbol, interval, mins, open_ts, close_ts)
                base_vol = base.volume if base else 0
                state = update_bucket(base, price_dec, volume=base_vol + size)
                tr = BucketTracker(open_ts=open_ts, close_ts=close_ts, state=state)
            else:
                state = update_bucket(tr.state, price_dec, volume=tr.state.volume + size)
                tr.state = state
            self._buckets[key] = tr
        self._dirty.add(symbol)

    def _seed(self, symbol, interval, mins, open_ts, close_ts):
        """大周期当前桶用已收线 5m bar 补基线 (重启/中途订阅防 open 漂移)。"""
        if self._repo is None or mins <= 5:
            return None
        try:
            src = self._repo.fetch_history_paged("us", symbol, "5m", before=close_ts, limit=mins // 5)
            src = [b for b in src if open_ts < b.ts <= close_ts]
            return seed_baseline(src)
        except Exception:  # noqa: BLE001
            return None

    async def _scan_subs(self) -> None:
        """刷新订阅: state:subscribe:us:{symbol}:{interval}。"""
        subs: dict[str, set[str]] = {}
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:us:*", count=200)
                for k in found:
                    kk = k.decode() if isinstance(k, bytes) else k
                    parts = kk.split(":")
                    if len(parts) >= 5 and parts[4] in INTERVAL_MIN:
                        subs.setdefault(parts[3], set()).add(parts[4])
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("us_hub.scan_failed", error=str(e))
        self._subs = subs

    async def _flush(self, *, now: datetime) -> None:
        # 1. 桶滚动 provisional (填 SIP 洞)
        for (symbol, interval), tr in list(self._just_closed.items()):
            await self._ticker.publish_provisional(symbol, interval, tr)
        self._just_closed.clear()
        # 2. dirty 标的: 分时 + 进行中态
        dirty = list(self._dirty)
        self._dirty.clear()
        for symbol in dirty:
            acc = self._accums.get(symbol)
            if acc is not None:
                try:
                    await self._writer.flush(symbol, acc, now=now)
                except Exception as e:  # noqa: BLE001
                    log.warning("us_hub.writer_failed", symbol=symbol, error=str(e))
            for interval in self._subs.get(symbol, set()):
                tr = self._buckets.get((symbol, interval))
                if tr is not None:
                    try:
                        await self._ticker.publish_current(symbol, interval, tr)
                    except Exception as e:  # noqa: BLE001
                        log.warning("us_hub.ticker_failed",
                                    symbol=symbol, interval=interval, error=str(e))

    async def run(self) -> None:
        log.info("us_trade_hub.started")
        tick = 0
        while not self._stopped:
            try:
                if tick % SUBS_REFRESH_TICKS == 0:
                    await self._scan_subs()
                tick += 1
                if is_trading_day("us") and is_market_session_open("us"):
                    await self._flush(now=datetime.now(timezone.utc))
            except Exception as e:  # noqa: BLE001
                log.warning("us_hub.loop_error", error=str(e))
            await asyncio.sleep(FLUSH_INTERVAL_S)


async def run_trade_hub(hub: TradeHub) -> None:
    await hub.run()
