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

    # === Binance WS 长连(P3,增量 push)===
    from apps.collector.crypto.ws_consumer import consume_loop as ws_consume_loop
    ws_task = asyncio.create_task(
        ws_consume_loop(
            repo=bar_repo, redis_bars=redis_bars, redis_cache=redis_cache,
        ),
        name="crypto.ws_consumer",
    )

    # === APScheduler: 每日兜底回填 + CD 信号扫描 ===
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

    # CD 信号扫描: 事件驱动(订阅 bus:bars.updated, 只读已存 bar)。
    # 不再用 attach_crypto_signal_jobs cron(那条走 fetch_fresh_bars 现聚合, 有 close/open 偏移)。
    from apps.api.deps import get_signal_scan_service
    from apps.collector.jobs.signal_scan_consumer import run_signal_scan_consumer
    _scan_svc = get_signal_scan_service()
    scan_consumer_task = asyncio.create_task(
        run_signal_scan_consumer(
            redis_cache._r, consumer_id=f"scan-crypto-{os.getpid()}",  # noqa: SLF001
            scan_fn=_scan_svc.scan_symbol_readonly, market="crypto",
        ),
        name="crypto.signal_scan_consumer",
    )

    sched.start()
    log.info(
        "collector_crypto.started",
        adapter="binance",
        backfill_task="crypto.backfill_initial",
        ws_task="crypto.ws_consumer",
        cron="04:00 UTC daily",
    )

    try:
        yield
    finally:
        sched.shutdown(wait=False)
        ws_task.cancel()
        backfill_task.cancel()
        scan_consumer_task.cancel()
        for t in (ws_task, backfill_task, scan_consumer_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await adapter.aclose()
        except Exception:
            pass
        log.info("collector_crypto.shutdown")


app = health_app("collector_crypto")
app.router.lifespan_context = lifespan

# 只读历史分页接口 (module 级挂载; repo 请求时惰性解析 —— lifespan 已 set override)
from apps.collector.base import attach_bars_history_route  # noqa: E402
from apps.api.deps import get_bar_repo  # noqa: E402
attach_bars_history_route(app, get_bar_repo, "crypto")


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
