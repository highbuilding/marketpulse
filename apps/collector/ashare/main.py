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
setup_proxy_and_logging("collector_ashare", use_proxy=False)

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

# module 级可变容器: lifespan 注入 intraday_repo, attach_intraday_route 惰性读取
_intraday_repo_holder: dict = {"repo": None}


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

    # === 分时 IntradayLineRepo ===
    from core.persistence.intraday_repo import IntradayLineRepo
    intraday_repo = IntradayLineRepo(str(_DATA / "intraday_ashare.duckdb"))
    _intraday_repo_holder["repo"] = intraday_repo

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

    # 常驻 akshare worker 池: 复用进程省去每次 ak_call 冷启动重 import akshare
    # (~0.8s CPU)。并发上限=池大小, 天然削峰防开盘 CPU 打满。
    from core.integrations.akshare import init_worker_pool
    from core.domain.runtime_env import tiered_int as _tiered_int_pool
    await init_worker_pool(_tiered_int_pool("AK_WORKER_POOL_SIZE", test=2, prod=6))

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

    # === scheduler: 仅 A 股专属 cron ===
    from core.scheduler.scheduler import (
        attach_ai_packet_job, attach_chip_preload_job,
        attach_fundamentals_jobs, attach_market_dashboard_job,
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
    # CD 信号扫描: 事件驱动(订阅 bus:bars.updated, 只读已存 bar)。
    # 不再用 attach_signal_jobs cron(那条走 fetch_fresh_bars 现聚合, 有 close/open 偏移)。
    from apps.collector.jobs.signal_scan_consumer import run_signal_scan_consumer
    from core.services.signal_service import SignalScanService
    from apps.api.deps import get_signal_repo
    _scan_svc = SignalScanService(get_kline_service(), get_signal_repo(), redis=_redis_for_mw)
    scan_consumer_task = asyncio.create_task(
        run_signal_scan_consumer(
            _redis_for_mw, consumer_id=f"scan-ashare-{os.getpid()}",
            scan_fn=_scan_svc.scan_symbol_readonly, market="ashare",
        ),
        name="ashare.signal_scan_consumer",
    )
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

    # === A 股按需 K 线轮询 (SSE 订阅驱动 + 大盘默认) ===
    from apps.collector.ashare.bar_poller import run_bar_poller
    from core.domain.runtime_env import tiered_int as _tiered_int
    # 冷启动让路 reconcile: poller 延迟启动, 避免与 reconcile 并发同拉 5m 触发 sina 限频。
    # test 标的少 reconcile 快(~30 标的); prod ~300 标的 reconcile 久, 延迟拉长。
    _poller_delay = _tiered_int("BAR_POLLER_STARTUP_DELAY_S", test=45, prod=180)
    _poller_task = asyncio.create_task(
        run_bar_poller(bar_repo, redis_cache, registry.get("ashare"),
                       startup_delay_s=_poller_delay),
        name="ashare.bar_poller",
    )

    # === A 股进行中 bar ticker (quote 驱动, 10s 一次) ===
    from apps.collector.ashare.quote_bar_ticker import run_quote_bar_ticker
    _ticker_task = asyncio.create_task(
        run_quote_bar_ticker(redis_cache, bar_repo),
        name="ashare.quote_bar_ticker",
    )

    # === A 股分时图写入器 (quote 驱动, 10s 一次) ===
    from apps.collector.ashare.intraday_line_writer import run_intraday_line_writer
    _intraday_task = asyncio.create_task(
        run_intraday_line_writer(intraday_repo, redis_cache),
        name="ashare.intraday_line_writer",
    )

    # === 标的集 (CORE ∪ watchlist): 供 startup_reconcile + daily_settlement 用 ===
    from datetime import datetime, timedelta, timezone
    from core.domain.core_symbols import core_symbols as _core_symbols
    from core.domain.markets import infer_market as _infer_market
    _wl_syms = await get_watchlist_service().dynamic_universe()
    _sweep_syms = sorted({s for s in (set(_wl_syms) | set(_core_symbols("ashare")))
                          if _infer_market(s) == "ashare"})
    log.info("ashare.symbols_ready", market="ashare", count=len(_sweep_syms))
    # 移除 (2026-06-08): ashare:sweep_derived 120min 聚合 cron。按"K线采集链改事件驱动,
    # 消除 cron"重构 —— 派生聚合改由三路事件/启动路径覆盖, 不再 cron 兜底:
    #   1) 5m 收线事件驱动聚合 intraday 派生 (bar_poller→aggregate_and_publish, 2天窗自愈)
    #   2) daily_settlement 收盘结算管 1d→1wk/1mo resample
    #   3) startup_reconcile 启动时全量初始化 + 缺口补齐 (含派生全周期)

    # === 启动 reconcile: CORE ∪ watchlist 缺口回补 (非阻塞) ===
    from apps.collector.startup_reconcile import run_startup_reconcile
    _reconcile_task = asyncio.create_task(
        run_startup_reconcile("ashare", bar_repo, kline, _sweep_syms),
        name="ashare.startup_reconcile",
    )

    # === 收盘结算 (完成度驱动, 非 cron): 1d 唯一的稳态写入路径 ===
    # 检测 session open→closed 边沿, 收盘后条件等待拉齐当日 1d + resample 1wk/1mo,
    # 全就位才转空闲。根治"1d 只在启动 reconcile 写一次, 之后全天不更新"。
    from apps.collector.ashare.daily_settlement import run_daily_settlement

    async def _settle_symbols_provider() -> list[str]:
        _wl = await get_watchlist_service().dynamic_universe()
        return sorted({s for s in (set(_wl) | set(_core_symbols("ashare")))
                       if _infer_market(s) == "ashare"})

    _settlement_task = asyncio.create_task(
        run_daily_settlement(kline, bar_repo, _settle_symbols_provider),
        name="ashare.daily_settlement",
    )

    # === 分时数据 90 天 purge (每日 02:30 UTC) ===
    from apscheduler.triggers.cron import CronTrigger

    async def _purge_intraday() -> None:
        from datetime import datetime, timezone, timedelta
        repo = _intraday_repo_holder["repo"]
        if repo is None:
            return
        try:
            repo.purge_before(datetime.now(timezone.utc) - timedelta(days=90))
            log.info("intraday.purged", before_days=90)
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.purge_failed", error=str(e))

    sched.add_job(
        _purge_intraday, CronTrigger(hour=2, minute=30),
        id="ashare:intraday_purge", max_instances=1, coalesce=True,
    )

    # CD 信号摘要邮件: 30min 攒批(复用 NotificationService.maybe_send_summary)。
    # 挂 ashare collector(常驻)+ _leader_gated 单发, 三市场各发一次。
    from core.scheduler.scheduler import _leader_gated as _lg
    _notify_svc = get_notification_service()

    async def _signal_digest():
        for _mkt in ("ashare", "us", "crypto"):
            try:
                await _notify_svc.maybe_send_summary(_mkt)
            except Exception as e:  # noqa: BLE001
                log.warning("signal_digest.failed", market=_mkt, error=str(e))

    sched.add_job(
        _lg(_signal_digest), CronTrigger(minute="*/30"),
        id="signal:digest", max_instances=1, coalesce=True,
    )

    # CD 信号补扫兜底: 30min 对全标的全信号周期跑 scan_symbol_readonly, 捞回漏事件。
    # 完整性兜底(事件可能被 maxlen 挤出 / aggregate 失败); 同只读路径, 幂等不引偏移。
    from apps.collector.jobs.signal_sweep_worker import sweep_symbols_for_market
    from core.domain.core_symbols import core_symbols as _core_syms_fn
    from core.domain.markets import infer_market as _infer_mkt

    async def _signal_sweep():
        for _mkt in ("ashare", "us", "crypto"):
            try:
                _wl = await get_watchlist_service().dynamic_universe()
                _syms = sorted({s for s in (set(_wl) | set(_core_syms_fn(_mkt)))
                                if _infer_mkt(s) == _mkt})
                await sweep_symbols_for_market(_scan_svc, _syms, market=_mkt)
            except Exception as e:  # noqa: BLE001
                log.warning("signal_sweep.market_failed", market=_mkt, error=str(e))

    sched.add_job(
        _lg(_signal_sweep), CronTrigger(minute="*/30"),
        id="signal:sweep", max_instances=1, coalesce=True,
    )
    log.info("bar_poller.bootstrapped")

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
        _poller_task.cancel()
        try:
            await _poller_task
        except (asyncio.CancelledError, Exception):
            pass
        _ticker_task.cancel()
        try:
            await _ticker_task
        except (asyncio.CancelledError, Exception):
            pass
        _intraday_task.cancel()
        try:
            await _intraday_task
        except (asyncio.CancelledError, Exception):
            pass
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
        _settlement_task.cancel()
        try:
            await _settlement_task
        except (asyncio.CancelledError, Exception):
            pass
        sched.shutdown(wait=False)
        try:
            from core.integrations.akshare import close_worker_pool
            await close_worker_pool()
        except Exception:
            pass
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

# 分时只读接口 (module 级挂载; repo 请求时惰性解析 —— lifespan 已注入 _intraday_repo_holder)
from apps.collector.base import attach_intraday_route  # noqa: E402
attach_intraday_route(app, lambda: _intraday_repo_holder["repo"], "ashare")


def main() -> None:
    port = int(os.getenv("COLLECTOR_ASHARE_PORT", "8788"))
    uvicorn.run("apps.collector.ashare.main:app", host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
