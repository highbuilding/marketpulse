"""crypto collector 进程入口 (P2).

职责 (P2):
- 启动 BinanceAdapter (REST)
- 启动时一次性回填 5 标的 × 8 周期全周期历史 (asyncio task)
- APScheduler 每日 04:00 UTC 兜底再跑一次
- 注入本市场 BarRepo (bars_crypto.duckdb) 给 api 进程读路径

P3 会接 binance_ws_consumer 增量推送。
P4 会接 SSE 路由(在 api 侧)。

参考: docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md
"""
from __future__ import annotations

from apps.collector.base import setup_proxy_and_logging
setup_proxy_and_logging("collector_crypto")

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from apps.collector.base import health_app, install_async_exception_handler

log = structlog.get_logger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_DATA = _BASE / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("collector_crypto.boot")
    install_async_exception_handler()

    # === 注入本市场 BarRepo ===
    from apps.api.deps import set_bar_repo_override
    from core.persistence.duckdb_repo import BarRepo
    bar_repo = BarRepo(str(_DATA / "bars_crypto.duckdb"))
    bar_repo.init()
    set_bar_repo_override(bar_repo)

    # === Redis cache + bars cache (供回填写 tail) ===
    from apps.api.deps import get_redis_cache, get_redis_bars_cache
    redis_cache = get_redis_cache()
    redis_ok = await redis_cache.ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning(
            "redis.unavailable_at_startup",
            note="collector_crypto 将继续运行,Redis tail 写入会失败但不影响 DuckDB",
        )
    redis_bars = get_redis_bars_cache()

    # === BinanceAdapter ===
    from core.adapters.binance import BinanceAdapter
    adapter = BinanceAdapter()

    # === 启动时一次性回填 ===
    from apps.collector.crypto.backfill import run_backfill
    backfill_task = asyncio.create_task(
        run_backfill(adapter, bar_repo, redis_bars), name="crypto.backfill_initial"
    )

    # === APScheduler 每日 04:00 UTC 兜底 ===
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        run_backfill,
        CronTrigger(hour=4, minute=0),
        args=(adapter, bar_repo, redis_bars),
        id="crypto:backfill_daily",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    log.info(
        "collector_crypto.started",
        adapter="binance",
        backfill_task="crypto.backfill_initial",
        cron="04:00 UTC daily",
    )

    try:
        yield
    finally:
        sched.shutdown(wait=False)
        backfill_task.cancel()
        try:
            await backfill_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await adapter.aclose()
        except Exception:
            pass
        log.info("collector_crypto.shutdown")


app = health_app("collector_crypto")
app.router.lifespan_context = lifespan


def main() -> None:
    port = int(os.getenv("COLLECTOR_CRYPTO_PORT", "8790"))
    uvicorn.run(
        "apps.collector.crypto.main:app",
        host="127.0.0.1",
        port=port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
