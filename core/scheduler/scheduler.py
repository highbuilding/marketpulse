from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo
from core.scheduler.fundamentals_jobs import (
    pull_north_flow_job, pull_watchlist_symbol_flow_job,
    purge_fund_flow_job, refresh_sectors_job,
)
from core.scheduler.jobs import flush_quotes_to_duckdb, tick_snapshot_once
from core.services.fund_flow_service import FundFlowService
from core.services.sector_service import SectorService
from core.services.watchlist_service import WatchlistService

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


def attach_fundamentals_jobs(
    sched: AsyncIOScheduler,
    *, fund_flow: FundFlowService, watchlist: WatchlistService, sector: SectorService,
) -> None:
    sched.add_job(
        pull_north_flow_job, IntervalTrigger(minutes=1),
        args=(fund_flow,),
        id="ff:north", max_instances=1, coalesce=True,
    )
    sched.add_job(
        pull_watchlist_symbol_flow_job, IntervalTrigger(minutes=30),
        args=(fund_flow, watchlist),
        id="ff:symbols", max_instances=1, coalesce=True,
    )
    sched.add_job(
        refresh_sectors_job, CronTrigger(hour=9, minute=25),
        args=(sector,),
        id="sectors:refresh", max_instances=1, coalesce=True,
    )
    sched.add_job(
        purge_fund_flow_job, CronTrigger(hour=2, minute=0),
        args=(fund_flow,),
        id="ff:purge", max_instances=1, coalesce=True,
    )
    log.info("scheduler.fundamentals_attached")
