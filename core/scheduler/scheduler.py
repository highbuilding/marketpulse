from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo
from core.scheduler.jobs import flush_quotes_to_duckdb, tick_snapshot_once

log = structlog.get_logger(__name__)


def build_scheduler(
    registry: AdapterRegistry, cache: QuoteCache, bar_repo: BarRepo,
) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    for market in registry.markets():
        sched.add_job(
            tick_snapshot_once, IntervalTrigger(seconds=10),
            args=(market, registry, cache),
            id=f"tick:{market}", max_instances=1, coalesce=True,
            misfire_grace_time=30,
        )
        sched.add_job(
            flush_quotes_to_duckdb, IntervalTrigger(seconds=60),
            args=(market, cache, bar_repo),
            id=f"flush:{market}", max_instances=1, coalesce=True,
        )
    log.info("scheduler.built", markets=registry.markets())
    return sched
