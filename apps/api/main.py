from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

# 必须在 import adapters 之前 load .env, 否则 USAdapter().__init__ 拿不到 ALPACA_*
from dotenv import load_dotenv
load_dotenv()

# 必须在其他 import 之前装好日志, 让 startup 期任何错误也能落到 data/logs/
from core.integrations.logging_setup import setup_logging
setup_logging()

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.deps import (
    get_bar_repo, get_fund_flow_service, get_kline_service,
    get_notification_service, get_quote_cache, get_registry,
    get_signal_scan_service, get_state_repo,
    get_symbol_directory_service, get_watchlist_service,
)
from apps.api.routes import (
    ai_market, cd_signals, health, indices, market_extras, north_flow,
    notifications, symbols, watchlists,
)
from apps.api.ws import ticks
from core.scheduler.scheduler import (
    attach_fundamentals_jobs, attach_signal_jobs, attach_us_signal_jobs,
    build_scheduler,
)

log = structlog.get_logger(__name__)


async def _async_refresh_directory(svc) -> None:
    # 延迟 5s 启动,避免和 scheduler 其他用 mini_racer 的 job 同时初始化导致 C 层崩溃
    try:
        await asyncio.sleep(5)
        n = await svc.refresh_ashare()
        log.info("directory.bootstrapped", count=n)
    except Exception as e:  # noqa: BLE001
        log.warning("directory.bootstrap_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Plan 1: ping Redis,失败仅 warning 不阻塞 (优雅降级)
    from apps.api.deps import get_redis_cache
    redis_ok = await get_redis_cache().ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="api 将退化到 DB 直读模式,直到 Redis 恢复")

    state_repo = get_state_repo()
    await state_repo.init()

    # Plan 2: bootstrap default watchlist
    await get_watchlist_service().bootstrap_default()

    # Plan 2.1: bootstrap index seeds(同步,快);A 股目录只在首次为空时刷新一次。
    # 注: ak.stock_zh_a_spot 在 macOS py_mini_racer 里跑过后会污染进程 V8 状态,
    # 导致后续任何 mini_racer 调用 SIGABRT。所以平时启动跳过, 让现有 directory 行存活。
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
        sched,
        fund_flow=get_fund_flow_service(),
        watchlist=get_watchlist_service(),
    )
    attach_signal_jobs(
        sched,
        signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=get_kline_service(),
    )
    attach_us_signal_jobs(
        sched,
        signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=get_kline_service(),
    )
    sched.start()
    log.info("app.started", markets=registry.markets())
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        log.info("app.stopped")


app = FastAPI(title="MarketPulse", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ai_market.router)
app.include_router(market_extras.router)
app.include_router(symbols.router)
app.include_router(watchlists.router)
app.include_router(north_flow.router)
app.include_router(indices.router)
app.include_router(cd_signals.router)
app.include_router(notifications.router)
app.include_router(ticks.router)
