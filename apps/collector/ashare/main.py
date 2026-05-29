"""A 股 collector 进程入口.

职责:
- A 股 tick:ashare (10s) + flush:all (1m, A 股 only registry)
- A 股 fetch_intraday cron (5m)
- A 股 cd:* signal scan (含 1d/4h/60m/30m/15m)
- A 股 fund_flow / chip / index_minute / market_top / ai_packet / dashboard / baseline
- 写自己的 bars_ashare.duckdb (RW)

参考: docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md
"""
from __future__ import annotations

from apps.collector.base import setup_proxy_and_logging
setup_proxy_and_logging("collector_ashare")

import asyncio
import os
import socket
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
    log.info("collector_ashare.boot")
    install_async_exception_handler()

    # === 注入本市场 BarRepo (必须在调用任何依赖 get_bar_repo 的工厂前) ===
    from apps.api.deps import set_bar_repo_override
    from core.persistence.duckdb_repo import BarRepo
    bar_repo = BarRepo(str(_DATA / "bars_ashare.duckdb"))
    bar_repo.init()
    set_bar_repo_override(bar_repo)

    # === Redis ===
    from apps.api.deps import get_redis_cache, get_redis_bars_cache
    redis_cache = get_redis_cache()
    redis_ok = await redis_cache.ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="collector_ashare 将继续运行,熔断/限速降级到内存态")
    redis_bars = get_redis_bars_cache()

    # === ak middleware (A 股 sina/em/ths) + Leader ===
    from core.cache.redis_client import make_redis
    from core.integrations import ak_middleware
    from core.integrations.breaker import SourceBreaker
    from core.integrations.outlets import LocalOutlet, OutletPool
    from core.integrations.ratelimit import RedisTokenBucket
    from apps.collector.leader import Leader

    _redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    _redis_for_mw = make_redis(_redis_url)
    _outlet_pool = OutletPool([LocalOutlet()], cache=redis_cache, cooling_seconds=1800)
    _breakers = {
        "sina": SourceBreaker(source="sina", cache=redis_cache),
        "em":   SourceBreaker(source="em",   cache=redis_cache),
        "ths":  SourceBreaker(source="ths",  cache=redis_cache),
    }
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

    # Leader (per-process, 进程拆开后每个 collector 独立 leader 锁; 互不干扰)
    from core.cache import keys as _keys
    _node_id = f"{socket.gethostname()}-ashare-{os.getpid()}"
    leader = Leader(redis=_redis_for_mw, node_id=_node_id,
                    lock_key=_keys.state_leader_collector_market("ashare"),
                    ttl_s=15, renew_interval_s=5)
    await leader.try_acquire_once()
    from core.scheduler.leader_gate import set_leader
    set_leader(leader)
    _leader_task = asyncio.create_task(leader.acquire_loop())
    log.info("leader.bootstrapped", node=_node_id, is_leader=leader.is_leader())

    # === refill consumer (A 股 watchlist 的按需补) ===
    from apps.collector.jobs.refill_consumer import consume_loop
    from apps.api.deps import get_kline_service
    kline = get_kline_service()

    async def _refill_dispatch(market: str, symbol: str, interval: str, days: int) -> None:
        if market != "ashare":
            return  # 各 collector 仅处理本市场的 refill request
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        await kline.fetch_fresh_bars(symbol, interval=interval, start=start, end=end)

    _refill_task = asyncio.create_task(
        consume_loop(_redis_for_mw, consumer_id=f"refill-ashare-{os.getpid()}",
                     refill_fn=_refill_dispatch),
    )
    log.info("refill_consumer.bootstrapped", market="ashare")

    # === 通用 SQLite 初始化 (state / watchlist / directory; 多进程共享, 幂等) ===
    from apps.api.deps import (
        get_state_repo, get_watchlist_service,
        get_symbol_directory_service,
    )
    state_repo = get_state_repo()
    await state_repo.init()
    await get_watchlist_service().bootstrap_default()
    dir_svc = get_symbol_directory_service()
    await dir_svc.bootstrap_seeds()
    if not os.getenv("MARKETPULSE_SKIP_DIR_BOOTSTRAP"):
        existing = await dir_svc.count()
        if existing < 100:
            asyncio.create_task(_async_refresh_directory(dir_svc))
        else:
            log.info("directory.skip_refresh", existing=existing)

    # === registry: 仅 A 股 adapter ===
    from core.adapters.registry import AdapterRegistry, load_sources_config
    config = load_sources_config(str(_BASE / "config" / "sources.yaml"))
    config["markets"] = {"ashare": config["markets"]["ashare"]}
    registry = AdapterRegistry.from_config(config)
    from core.cache.quote_cache import QuoteCache
    cache = QuoteCache(ttl_s=60)

    # === MarketAmountBaselineRepo ===
    from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo
    baseline_repo = MarketAmountBaselineRepo(str(_DATA / "state.db"))

    # === scheduler: 仅 A 股专属 cron ===
    from core.scheduler.scheduler import (
        attach_ai_packet_job, attach_baseline_persist_jobs, attach_chip_preload_job,
        attach_fundamentals_jobs, attach_index_minute_job, attach_market_dashboard_job,
        attach_market_top_job, attach_signal_jobs, build_scheduler,
    )
    from apps.api.deps import (
        get_ai_market_service, get_chip_service, get_fund_flow_service,
        get_market_query_service, get_notification_service, get_signal_scan_service,
    )
    sched = build_scheduler(
        registry, cache, bar_repo, get_watchlist_service(),
        redis_cache=redis_cache, redis_bars=redis_bars,
    )
    attach_fundamentals_jobs(
        sched, fund_flow=get_fund_flow_service(),
        watchlist=get_watchlist_service(),
    )
    attach_signal_jobs(
        sched, signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=kline,
    )
    attach_index_minute_job(sched, cache=redis_cache, baseline_repo=baseline_repo)
    attach_baseline_persist_jobs(sched, baseline_repo=baseline_repo)
    attach_market_dashboard_job(sched, cache=redis_cache)
    attach_market_top_job(sched,
                          market_query=get_market_query_service(),
                          cache=redis_cache)
    attach_ai_packet_job(sched,
                         ai_market=get_ai_market_service(),
                         cache=redis_cache)
    attach_chip_preload_job(sched,
                            chip_service=get_chip_service(),
                            watchlist=get_watchlist_service())
    sched.start()
    log.info("collector_ashare.started", markets=registry.markets())

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
        except Exception:
            pass
        log.info("collector_ashare.shutdown")


async def _async_refresh_directory(svc) -> None:
    """雷区 4: stock_zh_a_spot 跑过会污染 V8 状态,启动 5s 后再跑,且只在目录 < 100 行时跑。"""
    try:
        await asyncio.sleep(5)
        n = await svc.refresh_ashare()
        log.info("directory.bootstrapped", count=n)
    except Exception as e:  # noqa: BLE001
        log.warning("directory.bootstrap_failed", error=str(e))


app = health_app("collector_ashare")
app.router.lifespan_context = lifespan

# 只读历史分页接口 (module 级挂载; repo 请求时惰性解析 —— lifespan 已 set override)
from apps.collector.base import attach_bars_history_route  # noqa: E402
from apps.api.deps import get_bar_repo  # noqa: E402
attach_bars_history_route(app, get_bar_repo, "ashare")


def main() -> None:
    port = int(os.getenv("COLLECTOR_ASHARE_PORT", "8788"))
    uvicorn.run("apps.collector.ashare.main:app", host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
