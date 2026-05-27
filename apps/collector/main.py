"""Collector 进程入口。

职责:
- 跑 APScheduler (所有 cron / interval 任务,涵盖各市场 tick / flush /
  fundamentals / signal scan)
- 把 ak_call 全部局限在本进程
- 暴露 /health 给运维(8788)

绝对禁止: 暴露任何业务 HTTP 接口 — 那是 apps/api 的职责。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §1, §4.1
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from core.integrations.logging_setup import setup_logging
setup_logging()

import structlog
import uvicorn
from fastapi import FastAPI

from apps.api.deps import (
    get_bar_repo, get_fund_flow_service, get_kline_service,
    get_notification_service, get_quote_cache, get_redis_cache, get_registry,
    get_signal_scan_service, get_state_repo, get_symbol_directory_service,
    get_watchlist_service,
)
from core.scheduler.scheduler import (
    attach_fundamentals_jobs, attach_signal_jobs, attach_us_signal_jobs,
    build_scheduler,
)

log = structlog.get_logger(__name__)


async def _async_refresh_directory(svc) -> None:
    """与 apps/api/main.py 中同名函数行为一致。
    雷区 4: stock_zh_a_spot 跑过会污染 V8 状态,启动 5s 后再跑,且只在目录 < 100 行时跑。
    """
    try:
        await asyncio.sleep(5)
        n = await svc.refresh_ashare()
        log.info("directory.bootstrapped", count=n)
    except Exception as e:  # noqa: BLE001
        log.warning("directory.bootstrap_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("collector.boot")

    redis_ok = await get_redis_cache().ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="collector 将继续运行,熔断/限速降级到内存态")

    state_repo = get_state_repo()
    await state_repo.init()
    await get_watchlist_service().bootstrap_default()

    dir_svc = get_symbol_directory_service()
    await dir_svc.bootstrap_seeds()
    await dir_svc.bootstrap_us_seeds()
    if not os.getenv("MARKETPULSE_SKIP_DIR_BOOTSTRAP"):
        existing = await dir_svc.count()
        if existing < 100:
            asyncio.create_task(_async_refresh_directory(dir_svc))
        else:
            log.info("directory.skip_refresh", existing=existing)

    registry = get_registry()
    cache = get_quote_cache()
    bar_repo = get_bar_repo()

    sched = build_scheduler(registry, cache, bar_repo, get_watchlist_service())
    attach_fundamentals_jobs(
        sched, fund_flow=get_fund_flow_service(),
        watchlist=get_watchlist_service(),
    )
    attach_signal_jobs(
        sched, signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=get_kline_service(),
    )
    attach_us_signal_jobs(
        sched, signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=get_kline_service(),
    )
    sched.start()
    log.info("collector.started", markets=registry.markets())

    try:
        yield
    finally:
        sched.shutdown(wait=False)
        log.info("collector.shutdown")


# 一个最小的 FastAPI app,仅用于 /health(给运维 / honcho 探活)
app = FastAPI(title="MarketPulse Collector", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "role": "collector"}


def main() -> None:
    """uvicorn 入口。用 --host 127.0.0.1 + 内网端口,只暴露给运维。"""
    port = int(os.getenv("COLLECTOR_PORT", "8788"))
    uvicorn.run(
        "apps.collector.main:app",
        host="127.0.0.1",
        port=port,
        log_config=None,  # 沿用 setup_logging() 的 structlog
    )


if __name__ == "__main__":
    main()
