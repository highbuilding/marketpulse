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
from core.scheduler.signal_jobs import scan_cd_job
from core.services.fund_flow_service import FundFlowService
from core.services.sector_service import SectorService
from core.services.signal_service import SignalScanService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


def build_scheduler(
    registry: AdapterRegistry, cache: QuoteCache, bar_repo: BarRepo,
    watchlist: WatchlistService,
) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    for market in registry.markets():
        sched.add_job(
            tick_snapshot_once, IntervalTrigger(seconds=10),
            args=(market, registry, cache, watchlist),
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


def attach_signal_jobs(
    sched: AsyncIOScheduler,
    *, signal_scan: SignalScanService, watchlist: WatchlistService,
) -> None:
    """CD 信号扫描 — A 股交易日北京时间触发。

    APScheduler timezone='UTC', cron 用 UTC 时刻表示北京时间。
    BJT 10:35 = UTC 02:35 等。
    """
    common = dict(args=(signal_scan, watchlist), max_instances=1, coalesce=True,
                  misfire_grace_time=300)

    # 15m: BJT 10:00-15:30 ≈ UTC 02:00-07:30, 每 15 分钟扫一次
    # 跨午休/收盘时段顺带扫也无害(UNIQUE 幂等, 无新 bar 不写库)
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="2-7", minute="*/15"),
        id="cd:15m", kwargs={"interval": "15m", "market_filter": "ashare"}, **common,
    )

    # 30m: 同区间每 30 分钟一次
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="2-7", minute="*/30"),
        id="cd:30m", kwargs={"interval": "30m", "market_filter": "ashare"}, **common,
    )

    # 60m 收盘后 +5 分钟
    # BJT 10:30 / 11:30 / 14:30 / 15:00 收盘
    for h_utc, m_utc, tag in [(2, 35, "1030"), (3, 35, "1130"),
                                (6, 35, "1430"), (7, 5, "1500")]:
        sched.add_job(
            scan_cd_job, CronTrigger(day_of_week="mon-fri", hour=h_utc, minute=m_utc),
            id=f"cd:60m:{tag}", kwargs={"interval": "60m", "market_filter": "ashare"}, **common,
        )

    # 4h:A 股一天 1 根, 收盘后(BJT 15:10)
    sched.add_job(
        scan_cd_job, CronTrigger(day_of_week="mon-fri", hour=7, minute=10),
        id="cd:4h", kwargs={"interval": "4h", "market_filter": "ashare"}, **common,
    )

    # 1d:BJT 15:30
    sched.add_job(
        scan_cd_job, CronTrigger(day_of_week="mon-fri", hour=7, minute=30),
        id="cd:1d", kwargs={"interval": "1d", "market_filter": "ashare"}, **common,
    )
    log.info("scheduler.signal_jobs_attached")


def attach_us_signal_jobs(
    sched: AsyncIOScheduler,
    *, signal_scan: SignalScanService, watchlist: WatchlistService,
) -> None:
    """美股 CD 信号扫描 cron(ET 时区, APScheduler 自动跟夏/冬令时)。

    扫描区间: 盘前 04:00 ET 到盘后 20:00 ET, 共 16 小时。
    所有 job 带 market_filter='us', 避免与 A 股 cron 互相污染。
    """
    common = dict(args=(signal_scan, watchlist), max_instances=1, coalesce=True,
                  misfire_grace_time=300)
    et = "America/New_York"

    # 15m: ET 04:00-19:45 每 15 分钟
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/15", timezone=et),
        id="cd:us:15m",
        kwargs={"interval": "15m", "market_filter": "us"},
        **common,
    )

    # 30m: ET 04:00-19:30 每 30 分钟
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/30", timezone=et),
        id="cd:us:30m",
        kwargs={"interval": "30m", "market_filter": "us"},
        **common,
    )

    # 60m: 每根收盘 +5 min, ET 05:05 - 20:05 每小时
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="5-20", minute="5", timezone=et),
        id="cd:us:60m",
        kwargs={"interval": "60m", "market_filter": "us"},
        **common,
    )

    # 4h: 收盘点 ET 08:00 / 12:00 / 16:00 / 20:00, 各 +5
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="8,12,16,20", minute="5", timezone=et),
        id="cd:us:4h",
        kwargs={"interval": "4h", "market_filter": "us"},
        **common,
    )

    # 1d: 盘后定稿 ET 20:05
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="20", minute="5", timezone=et),
        id="cd:us:1d",
        kwargs={"interval": "1d", "market_filter": "us"},
        **common,
    )
    log.info("scheduler.us_signal_jobs_attached")
