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
from core.domain.runtime_env import tiered_int
from apps.collector.jobs.aggregate_derived import aggregate_and_publish

log = structlog.get_logger(__name__)

# 轮询间隔 (秒): 按 APP_ENV 分层。test=10s; prod=60s(Alpaca 限频宽松, ~100 标的串行)。
# US_POLL_INTERVAL_S 环境变量可显式覆盖。
POLL_INTERVAL_S = tiered_int("US_POLL_INTERVAL_S", test=10, prod=60)
_POLL_INTERVALS = ("5m",)  # 只直取 5m; 15m/30m/60m/4h 由 5m 聚合派生
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
                targets=("15m", "30m", "60m", "4h"), now=datetime.now(timezone.utc))

    async def _scan_symbols(self) -> set[str]:
        """采集集 = CORE 常驻(与前端订阅解耦)。"""
        return set(core_symbols("us"))

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


async def run_us_bar_poller(repo, redis, adapter, *, startup_delay_s: int = 0) -> None:
    """collector lifespan 启动。

    startup_delay_s: 冷启动让路给 startup_reconcile —— reconcile 直拉 1d/60m/4h +
    poller 拉 5m 都打 Alpaca, 并发叠加增大限频/连接压力。延迟启动 poller, 让 reconcile
    先把历史拉全, poller 再接管 live 收线轮询。
    """
    if startup_delay_s > 0:
        log.info("us_bar_poller.startup_delay", seconds=startup_delay_s)
        await asyncio.sleep(startup_delay_s)
    await UsBarPoller(repo, redis, adapter).run()
