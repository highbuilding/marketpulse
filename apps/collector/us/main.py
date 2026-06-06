"""美股 collector 进程入口.

职责:
- 美股 tick:us (10s, Alpaca latest_quote)
- 美股 fetch_intraday cron (5m / 15m / 30m / 60m / 4h)
- 美股 cd:* signal scan (ET 时段)
- 美股 us_index_minute (SPY/QQQ/DIA ETF 代理)
- 写自己的 bars_us.duckdb (RW)

参考: docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md
"""
from __future__ import annotations

from apps.collector.base import setup_proxy_and_logging
setup_proxy_and_logging("collector_us")

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
    log.info("collector_us.boot")
    install_async_exception_handler()

    # === 注入本市场 BarRepo ===
    from apps.api.deps import set_bar_repo_override
    from core.persistence.duckdb_repo import BarRepo
    bar_repo = BarRepo(str(_DATA / "bars_us.duckdb"))
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
                    note="collector_us 将继续运行,熔断/限速降级到内存态")
    redis_bars = get_redis_bars_cache()

    # === ak middleware (美股不走 ak_call, 但保留初始化无副作用 — outlet/breaker 共享决策) ===
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

    # Leader (per-market lock)
    from core.cache import keys as _keys
    _node_id = f"{socket.gethostname()}-us-{os.getpid()}"
    leader = Leader(redis=_redis_for_mw, node_id=_node_id,
                    lock_key=_keys.state_leader_collector_market("us"),
                    ttl_s=15, renew_interval_s=5)
    await leader.try_acquire_once()
    from core.scheduler.leader_gate import set_leader
    set_leader(leader)
    _leader_task = asyncio.create_task(leader.acquire_loop())
    log.info("leader.bootstrapped", node=_node_id, is_leader=leader.is_leader())

    # === refill consumer (美股 watchlist 的按需补) ===
    from apps.collector.jobs.refill_consumer import consume_loop
    from apps.api.deps import get_kline_service
    kline = get_kline_service()

    async def _refill_dispatch(market: str, symbol: str, interval: str, days: int) -> None:
        if market != "us":
            return  # 仅本市场
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        await kline.fetch_fresh_bars(symbol, interval=interval, start=start, end=end)

    _refill_task = asyncio.create_task(
        consume_loop(_redis_for_mw, consumer_id=f"refill-us-{os.getpid()}",
                     refill_fn=_refill_dispatch),
    )
    log.info("refill_consumer.bootstrapped", market="us")

    # === 通用 SQLite 初始化 ===
    from apps.api.deps import (
        get_state_repo, get_watchlist_service,
        get_symbol_directory_service,
    )
    state_repo = get_state_repo()
    await state_repo.init()
    await get_watchlist_service().bootstrap_default()
    dir_svc = get_symbol_directory_service()
    await dir_svc.bootstrap_seeds()
    await dir_svc.bootstrap_us_seeds()

    # === registry: 仅 美股 adapter ===
    from core.adapters.registry import AdapterRegistry, load_sources_config
    config = load_sources_config(str(_BASE / "config" / "sources.yaml"))
    config["markets"] = {"us": config["markets"]["us"]}
    registry = AdapterRegistry.from_config(config)
    from core.cache.quote_cache import QuoteCache
    cache = QuoteCache(ttl_s=60)

    # === MarketAmountBaselineRepo (us_index_minute 也需要) ===
    from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo
    baseline_repo = MarketAmountBaselineRepo(str(_DATA / "state.db"))

    # === scheduler: 仅 美股专属 cron ===
    from core.scheduler.scheduler import (
        build_scheduler, attach_us_signal_jobs, attach_us_index_minute_job,
    )
    from apps.api.deps import (
        get_notification_service, get_signal_scan_service,
    )
    sched = build_scheduler(
        registry, cache, bar_repo, get_watchlist_service(),
        redis_cache=redis_cache, redis_bars=redis_bars,
    )
    # CD 信号扫描: 事件驱动(订阅 bus:bars.updated, 只读已存 bar)。
    # 不再用 attach_us_signal_jobs cron(那条走 fetch_fresh_bars 现聚合, 有 close/open 偏移)。
    from apps.collector.jobs.signal_scan_consumer import run_signal_scan_consumer
    from core.services.signal_service import SignalScanService
    from apps.api.deps import get_signal_repo
    _scan_svc = SignalScanService(get_kline_service(), get_signal_repo(), redis=_redis_for_mw)
    scan_consumer_task = asyncio.create_task(
        run_signal_scan_consumer(
            _redis_for_mw, consumer_id=f"scan-us-{os.getpid()}",
            scan_fn=_scan_svc.scan_symbol_readonly, market="us",
        ),
        name="us.signal_scan_consumer",
    )
    attach_us_index_minute_job(sched, cache=redis_cache, baseline_repo=baseline_repo)
    sched.start()

    # === 美股实时链路: trades WS → TradeHub → writer/ticker; REST SIP poller 收线 ===
    from core.persistence.intraday_repo import IntradayLineRepo
    from apps.collector.us.intraday_line_writer import UsIntradayWriter
    from apps.collector.us.bar_ticker import UsBarTicker
    from apps.collector.us.trade_hub import TradeHub, run_trade_hub
    from apps.collector.us.bar_poller import run_us_bar_poller
    from apps.collector.us.ws_consumer import consume_loop as ws_consume_loop
    from apps.collector.us.main import set_us_intraday_repo_override

    intraday_repo = IntradayLineRepo(str(_DATA / "intraday_us.duckdb"))
    set_us_intraday_repo_override(intraday_repo)

    _us_writer = UsIntradayWriter(intraday_repo, redis_cache)
    _us_ticker = UsBarTicker(redis_cache)
    _hub = TradeHub(redis=redis_cache, repo=bar_repo, writer=_us_writer, ticker=_us_ticker)

    _ws_task = asyncio.create_task(ws_consume_loop(hub=_hub, redis=redis_cache), name="us.ws_consumer")
    _hub_task = asyncio.create_task(run_trade_hub(_hub), name="us.trade_hub")
    us_adapter = registry.get("us")
    from core.domain.runtime_env import tiered_int as _tiered_int
    # 冷启动让路 reconcile: poller 延迟启动, 避免与 reconcile(直拉 1d/60m/4h)并发叠加 Alpaca 压力。
    _us_poller_delay = _tiered_int("BAR_POLLER_STARTUP_DELAY_S", test=45, prod=180)
    _poller_task = asyncio.create_task(
        run_us_bar_poller(bar_repo, redis_cache, us_adapter,
                          startup_delay_s=_us_poller_delay), name="us.bar_poller")
    log.info("us_realtime.bootstrapped")

    # === 分时 90 天 purge cron ===
    async def _purge_intraday():
        from datetime import datetime, timezone, timedelta
        try:
            intraday_repo.purge_before(datetime.now(timezone.utc) - timedelta(days=90))
            log.info("us_intraday.purged", before_days=90)
        except Exception as e:  # noqa: BLE001
            log.warning("us_intraday.purge_failed", error=str(e))
    sched.add_job(_purge_intraday, "cron", hour=7, minute=30,
                  id="us:intraday_purge", max_instances=1, coalesce=True)

    # === 定期聚合派生周期 (60m/4h/1wk/1mo) ===
    from datetime import datetime, timedelta, timezone
    from apps.collector.jobs.aggregate_derived import sweep_derived
    from core.domain.core_symbols import core_symbols as _core_symbols
    from core.domain.markets import infer_market as _infer_market
    _wl_syms = await get_watchlist_service().dynamic_universe()
    _sweep_syms = sorted({s for s in (set(_wl_syms) | set(_core_symbols("us")))
                          if _infer_market(s) == "us"})
    log.info("sweep_derived.symbols_ready", market="us", count=len(_sweep_syms))
    sched.add_job(
        sweep_derived,
        "interval", minutes=120,
        args=(bar_repo, "us", _sweep_syms),
        id="us:sweep_derived",
        max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
    )

    # === 启动 reconcile: CORE ∪ watchlist 缺口回补 (非阻塞) ===
    from apps.collector.startup_reconcile import run_startup_reconcile
    _reconcile_task = asyncio.create_task(
        run_startup_reconcile("us", bar_repo, kline, _sweep_syms),
        name="us.startup_reconcile",
    )

    log.info("collector_us.started", markets=registry.markets())

    try:
        yield
    finally:
        leader._stopped = True
        _leader_task.cancel()
        try:
            await _leader_task
        except (asyncio.CancelledError, Exception):
            pass
        _ws_task.cancel()
        try:
            await _ws_task
        except (asyncio.CancelledError, Exception):
            pass
        for _t in (_hub_task, _poller_task):
            _t.cancel()
            try:
                await _t
            except (asyncio.CancelledError, Exception):
                pass
        await leader.release()
        _refill_task.cancel()
        try:
            await _refill_task
        except (asyncio.CancelledError, Exception):
            pass
        _reconcile_task.cancel()
        try:
            await _reconcile_task
        except (asyncio.CancelledError, Exception):
            pass
        sched.shutdown(wait=False)
        try:
            await _redis_for_mw.aclose()
        except Exception:
            pass
        log.info("collector_us.shutdown")


app = health_app("collector_us")
app.router.lifespan_context = lifespan

# 只读历史分页接口 (module 级挂载; repo 请求时惰性解析 —— lifespan 已 set override)
from apps.collector.base import attach_bars_history_route  # noqa: E402
from apps.api.deps import get_bar_repo  # noqa: E402
attach_bars_history_route(app, get_bar_repo, "us")

# 分时只读接口 (module 级挂载, 惰性解析 repo —— lifespan set override)
from apps.collector.base import attach_intraday_route  # noqa: E402

_us_intraday_repo = None  # lifespan 内 set_us_intraday_repo_override 注入


def set_us_intraday_repo_override(repo) -> None:
    global _us_intraday_repo
    _us_intraday_repo = repo


attach_intraday_route(app, lambda: _us_intraday_repo, "us", get_bar_repo=get_bar_repo)


def main() -> None:
    port = int(os.getenv("COLLECTOR_US_PORT", "8789"))
    uvicorn.run("apps.collector.us.main:app", host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
