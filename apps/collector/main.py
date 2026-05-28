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
    get_bar_repo, get_chip_service, get_fund_flow_service, get_kline_service,
    get_market_query_service, get_notification_service, get_quote_cache,
    get_redis_cache, get_registry, get_signal_scan_service, get_state_repo,
    get_symbol_directory_service, get_watchlist_service,
)
from core.scheduler.scheduler import (
    attach_chip_preload_job, attach_fundamentals_jobs, attach_index_minute_job,
    attach_market_dashboard_job, attach_market_top_job, attach_signal_jobs,
    attach_us_signal_jobs, build_scheduler,
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

    # Plan 2: 初始化 ak_call 三层中间件 + Leader
    from core.cache.redis_client import make_redis
    from core.integrations import ak_middleware
    from core.integrations.breaker import SourceBreaker
    from core.integrations.outlets import LocalOutlet, OutletPool
    from core.integrations.ratelimit import RedisTokenBucket
    from apps.collector.leader import Leader
    import socket

    _redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    _redis_for_mw = make_redis(_redis_url)
    _redis_cache = get_redis_cache()

    # Outlet: 单一 LocalOutlet (无代理)
    _outlet_pool = OutletPool([LocalOutlet()], cache=_redis_cache, cooling_seconds=1800)

    # Breakers: per-source
    _breakers = {
        "sina": SourceBreaker(source="sina", cache=_redis_cache),
        "em": SourceBreaker(source="em", cache=_redis_cache),
        "ths": SourceBreaker(source="ths", cache=_redis_cache),
    }

    # Ratelimits: per-source 令牌桶 (rate=tok/s, burst=最大瞬时)
    _ratelimits = {
        "sina": RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:sina", rate=5, burst=20),
        "em":   RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:em",   rate=10, burst=50),
        "ths":  RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:ths",  rate=3, burst=10),
    }

    ak_middleware.setup(ak_middleware.AkMiddleware(
        outlet_pool=_outlet_pool, breakers=_breakers, ratelimits=_ratelimits,
    ))
    log.info("ak_middleware.ready",
             outlets=["local"], breakers=list(_breakers.keys()),
             ratelimits=list(_ratelimits.keys()))

    # Leader
    _node_id = f"{socket.gethostname()}-{os.getpid()}"
    leader = Leader(redis=_redis_for_mw, node_id=_node_id, ttl_s=15, renew_interval_s=5)
    await leader.try_acquire_once()
    from core.scheduler.leader_gate import set_leader
    set_leader(leader)
    _leader_task = asyncio.create_task(leader.acquire_loop())
    log.info("leader.bootstrapped", node=_node_id, is_leader=leader.is_leader())

    # Plan 2: refill consumer (订阅 bus:bars.refill_request, Plan 3 才有真正的 publisher)
    from apps.collector.jobs.refill_consumer import consume_loop

    kline = get_kline_service()

    async def _refill_dispatch(market: str, symbol: str, interval: str, days: int) -> None:
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        await kline.fetch_fresh_bars(symbol, interval=interval, start=start, end=end)

    _refill_task = asyncio.create_task(
        consume_loop(_redis_for_mw, consumer_id=f"refill-{os.getpid()}",
                     refill_fn=_refill_dispatch),
    )
    log.info("refill_consumer.bootstrapped")

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

    sched = build_scheduler(registry, cache, bar_repo, get_watchlist_service(),
                            redis_cache=_redis_cache)
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
    attach_index_minute_job(sched, cache=_redis_cache)
    attach_market_dashboard_job(sched, cache=_redis_cache)
    attach_market_top_job(sched,
                          market_query=get_market_query_service(),
                          cache=_redis_cache)
    attach_chip_preload_job(sched,
                            chip_service=get_chip_service(),
                            watchlist=get_watchlist_service())
    sched.start()
    log.info("collector.started", markets=registry.markets())

    try:
        yield
    finally:
        leader._stopped = True
        _leader_task.cancel()
        try:
            await _leader_task
        except (asyncio.CancelledError, Exception):
            pass
        await leader.release()
        _refill_task.cancel()
        try:
            await _refill_task
        except (asyncio.CancelledError, Exception):
            pass
        sched.shutdown(wait=False)
        try:
            await _redis_for_mw.aclose()
        except Exception:  # noqa: BLE001
            pass
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
