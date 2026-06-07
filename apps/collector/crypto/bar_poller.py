"""crypto 收线 REST 兜底 poller。

WS(ws_consumer)负责实时增量推送,但代理链路不稳时会出现"假活连接"——
连上却收不到任何帧,空转到 ping timeout 才被判死,期间收盘的低频 bar
(4h/1d/60m)的 final 帧被永久漏掉(Binance 不重发历史 final)。

本 poller 周期性对全周期跑 refresh_recent(REST 重拉最近 3 天 + 剔进行中半根
+ upsert 覆盖),把 WS 漏掉的收线根补齐。对齐 A股/美股 bar_poller 的
"WS 实时 + REST 收线兜底"双通道模式。

crypto 24/7,无交易日门控(与 us/ashare poller 区别)。
"""
from __future__ import annotations

import asyncio

import structlog

from apps.collector.crypto.backfill import INTERVALS, SYMBOLS, refresh_recent
from core.adapters.binance import BinanceAdapter
from core.cache.redis_bars_cache import RedisBarsCache
from core.domain.runtime_env import tiered_int
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

# 轮询间隔 (秒): 按 APP_ENV 分层。test=30s; prod=300s(5 分钟)。
# CRYPTO_POLL_INTERVAL_S 环境变量可显式覆盖。
# WS 漏掉的 final 最多 POLL_INTERVAL_S 后被 REST 补齐。
POLL_INTERVAL_S = tiered_int("CRYPTO_POLL_INTERVAL_S", test=30, prod=300)


class CryptoBarPoller:
    """周期性 REST 兜底: 复用 refresh_recent 补齐 WS 漏收的收线根。"""

    def __init__(
        self,
        adapter: BinanceAdapter,
        repo: BarRepo,
        redis_bars: RedisBarsCache,
    ) -> None:
        self._adapter = adapter
        self._repo = repo
        self._redis_bars = redis_bars
        self._stopped = False

    async def _sweep_once(self) -> None:
        """对 5 标的 × 8 周期串行跑一遍 refresh_recent。单条失败不阻塞整批。"""
        for symbol in SYMBOLS:
            for interval in INTERVALS:
                # refresh_recent 内部已 try/except 优雅降级(fetch/db/redis 各包)
                await refresh_recent(
                    self._adapter, self._repo, self._redis_bars, symbol, interval
                )
                await asyncio.sleep(0.2)  # 限流缓冲, 对齐 backfill

    async def run(self) -> None:
        log.info("crypto_bar_poller.started", poll_interval_s=POLL_INTERVAL_S)
        while not self._stopped:
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("crypto_poller.loop_error", error=str(e))
            await asyncio.sleep(POLL_INTERVAL_S)


async def run_crypto_bar_poller(
    adapter: BinanceAdapter,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
    *,
    startup_delay_s: int = 0,
) -> None:
    """collector lifespan 启动。

    startup_delay_s: 冷启动让路给 initial run_backfill —— backfill 直拉全史 +
    poller 周期兜底都打 Binance(经同一代理),并发叠加增大限频/连接压力。
    延迟启动 poller, 让 backfill 先把历史拉全, poller 再接管周期收线兜底。
    """
    if startup_delay_s > 0:
        log.info("crypto_bar_poller.startup_delay", seconds=startup_delay_s)
        await asyncio.sleep(startup_delay_s)
    await CryptoBarPoller(adapter, repo, redis_bars).run()
