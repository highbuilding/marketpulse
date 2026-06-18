"""A 股盘口异动 / 板块异动常驻采集 worker (asyncio loop, 非 cron)。

对齐 quote_bar_ticker / intraday_line_writer / bar_poller 的 worker 模式:
collector 内一条常驻循环按节拍拉取 → 写 cache, 数据随 cache 流向 api。
时段窗守卫在 refresh_* 内 (非交易时段/盘前盘后 no-op, 不打 EM)。

节拍: 个股异动 ~2min/轮; 板块异动 ~5min (每 N 轮一次, 较重)。
"""
from __future__ import annotations

import asyncio

import structlog

from apps.collector.jobs.market_changes import (
    refresh_ashare_board_changes,
    refresh_ashare_changes,
)
from core.cache.redis_client import RedisCache
from core.services.market_changes_service import MarketChangesService

log = structlog.get_logger(__name__)


async def run_market_changes_worker(
    service: MarketChangesService,
    cache: RedisCache,
    *,
    changes_interval_s: int = 120,
    board_every: int = 3,          # 每 3 轮 (~6min) 拉一次板块异动
    startup_delay_s: int = 20,
) -> None:
    await asyncio.sleep(startup_delay_s)
    log.info("market_changes_worker.started", interval_s=changes_interval_s)
    i = 0
    while True:
        try:
            await refresh_ashare_changes(service, cache)
            if i % board_every == 0:
                await refresh_ashare_board_changes(service, cache)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("market_changes_worker.tick_failed", error=str(e))
        i += 1
        await asyncio.sleep(changes_interval_s)
