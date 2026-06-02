"""美股收线源 (REST SIP)。周期拉 5m/15m/30m 已收线根 → 入库 + 发 final=true。

成交量权威 (SIP 全市场), 喂 CD 信号/量指标。延迟 ~15-20min (免费层),
最近窗的实时跳由 TradeHub(IEX trades) 的 provisional 兜底。
5m 收线触发 aggregate_and_publish(60m/4h)。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.domain.core_symbols import core_symbols
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
from apps.collector.jobs.aggregate_derived import aggregate_and_publish

log = structlog.get_logger(__name__)

POLL_INTERVAL_S = 60
_POLL_INTERVALS = ("5m", "15m", "30m")
_FREQ = {"5m": "5", "15m": "15", "30m": "30"}


class UsBarPoller:
    def __init__(self, repo, redis, adapter):
        self._repo = repo
        self._redis = redis
        self._adapter = adapter
        self._stopped = False

    async def poll_one(self, symbol: str, interval: str) -> None:
        try:
            bars = await self._adapter.fetch_intraday(symbol, _FREQ[interval])
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.fetch_failed", symbol=symbol, interval=interval, error=str(e))
            return
        if not bars:
            return
        try:
            existing = self._repo.fetch_history_paged("us", symbol, interval, before=None, limit=1)
            last_ts = existing[-1].ts if existing else None
        except Exception:  # noqa: BLE001
            last_ts = None
        fresh = [b for b in bars if last_ts is None or b.ts > last_ts]
        if not fresh:
            return
        try:
            self._repo.insert_bars(fresh)
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.db_write_failed", symbol=symbol, error=str(e))
        latest = fresh[-1]
        payload = {
            "market": "us", "symbol": symbol, "interval": interval,
            "ts": latest.ts.isoformat(), "open": float(latest.open),
            "high": float(latest.high), "low": float(latest.low),
            "close": float(latest.close), "volume": int(latest.volume), "final": True,
        }
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()}, maxlen=10000, approximate=True)
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.xadd_failed", error=str(e))
        if interval == "5m":
            await aggregate_and_publish(
                self._repo, self._redis, "us", symbol,
                targets=("60m", "4h"), now=datetime.now(timezone.utc))

    async def _scan_symbols(self) -> set[str]:
        active: set[str] = set()
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:us:*", count=200)
                for k in found:
                    kk = k.decode() if isinstance(k, bytes) else k
                    parts = kk.split(":")
                    if len(parts) >= 4:
                        active.add(parts[3])
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.scan_failed", error=str(e))
        active.update(core_symbols("us"))   # 美股 baseline: 核心标的无条件轮询(不依赖订阅)
        return active

    async def run(self) -> None:
        log.info("us_bar_poller.started")
        while not self._stopped:
            try:
                if is_trading_day("us") and is_market_session_open("us"):
                    for symbol in await self._scan_symbols():
                        for interval in _POLL_INTERVALS:
                            await self.poll_one(symbol, interval)
            except Exception as e:  # noqa: BLE001
                log.warning("us_poller.loop_error", error=str(e))
            await asyncio.sleep(POLL_INTERVAL_S)


async def run_us_bar_poller(repo, redis, adapter) -> None:
    await UsBarPoller(repo, redis, adapter).run()
