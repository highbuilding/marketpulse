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
from core.scheduler.signal_jobs import fetch_intraday_job, scan_cd_job
from core.services.fund_flow_service import FundFlowService
from core.services.kline_service import KLineService
from core.services.notification_service import NotificationService
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
    notify_service: NotificationService | None = None,
    kline: KLineService | None = None,
) -> None:
    """CD 信号扫描 — A 股交易日北京时间触发。

    APScheduler timezone='UTC', cron 用 UTC 时刻表示北京时间。
    BJT 10:35 = UTC 02:35 等。
    """
    common = dict(args=(signal_scan, watchlist, notify_service),
                  max_instances=1, coalesce=True, misfire_grace_time=300)

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

    # 60m 收盘后 +5 分钟 (富途口径: 10:30 / 11:30 / 14:00 / 15:00 close)
    for h_utc, m_utc, tag in [(2, 35, "1030"), (3, 35, "1130"),
                                (6, 5, "1400"), (7, 5, "1500")]:
        sched.add_job(
            scan_cd_job, CronTrigger(day_of_week="mon-fri", hour=h_utc, minute=m_utc),
            id=f"cd:60m:{tag}", kwargs={"interval": "60m", "market_filter": "ashare"}, **common,
        )

    # 4h: A 股富途口径 2 根/日 (close BJT 11:30 / 15:00)
    for h_utc, m_utc, tag in [(3, 35, "1130"), (7, 5, "1500")]:
        sched.add_job(
            scan_cd_job, CronTrigger(day_of_week="mon-fri", hour=h_utc, minute=m_utc),
            id=f"cd:4h:{tag}", kwargs={"interval": "4h", "market_filter": "ashare"}, **common,
        )

    # 1d:BJT 15:30
    sched.add_job(
        scan_cd_job, CronTrigger(day_of_week="mon-fri", hour=7, minute=30),
        id="cd:1d", kwargs={"interval": "1d", "market_filter": "ashare"}, **common,
    )
    # 5m: is_signal=False, 不扫信号, 只入库给详情页 K 线用。BJT 09:30-15:00,
    # 每 15 min 跑一次(sina 不限频, 但太密无意义)
    if kline is not None:
        sched.add_job(
            fetch_intraday_job,
            CronTrigger(day_of_week="mon-fri", hour="1-7", minute="*/15"),
            args=(kline, watchlist),
            kwargs={"interval": "5m", "market_filter": "ashare"},
            id="fetch:ashare:5m",
            max_instances=1, coalesce=True, misfire_grace_time=300,
        )
    log.info("scheduler.signal_jobs_attached")


def attach_us_signal_jobs(
    sched: AsyncIOScheduler,
    *, signal_scan: SignalScanService, watchlist: WatchlistService,
    notify_service: NotificationService | None = None,
    kline: KLineService | None = None,
) -> None:
    """美股 CD 信号扫描 cron(ET 时区, APScheduler 自动跟夏/冬令时)。

    扫描区间: 盘前 04:00 ET 到盘后 20:00 ET, 共 16 小时。
    所有 job 带 market_filter='us', 避免与 A 股 cron 互相污染。
    """
    common = dict(args=(signal_scan, watchlist, notify_service),
                  max_instances=1, coalesce=True, misfire_grace_time=300)
    et = "America/New_York"

    # 15m: ET 04:00-19:45 每 15 分钟, + 20:30 收尾扫 (盘后末根 close=20:00,
    # end_safe=20:10 时刚封口能拿到)
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/15", timezone=et),
        id="cd:us:15m",
        kwargs={"interval": "15m", "market_filter": "us"},
        **common,
    )
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour=20, minute=30, timezone=et),
        id="cd:us:15m:close",
        kwargs={"interval": "15m", "market_filter": "us"},
        **common,
    )

    # 30m: ET 04:00-19:30 每 30 分钟, + 20:30 收尾扫
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/30", timezone=et),
        id="cd:us:30m",
        kwargs={"interval": "30m", "market_filter": "us"},
        **common,
    )
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour=20, minute=30, timezone=et),
        id="cd:us:30m:close",
        kwargs={"interval": "30m", "market_filter": "us"},
        **common,
    )

    # 60m: 富途口径 17 根/日, 每根 close +5 min
    # 整点收盘: ET 05/06/07/08/09 (盘前) + 10:30/11:30/12:30/13:30/14:30/15:30 (RTH) + 17/18/19/20 (盘后)
    # 09:30 半棒收盘 → 09:35; 16:00 半棒收盘 → 16:05; 其他整点 +5
    # 用两条 cron 合并: 盘前/盘后整点(5/6/7/8/9/17/18/19/20):05 + RTH 半小时(10/11/12/13/14/15):35 + 边界 9:35 / 16:05
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="5-9,16-20", minute="5", timezone=et),
        id="cd:us:60m:hourly_05",
        kwargs={"interval": "60m", "market_filter": "us"}, **common,
    )
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="35", timezone=et),
        id="cd:us:60m:hourly_35",
        kwargs={"interval": "60m", "market_filter": "us"}, **common,
    )

    # 4h: 富途口径 5 根/日 (close ET 08:00 / 09:30 / 13:30 / 16:00 / 20:00)
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="8,16,20", minute="5", timezone=et),
        id="cd:us:4h:hourly",
        kwargs={"interval": "4h", "market_filter": "us"}, **common,
    )
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="9,13", minute="35", timezone=et),
        id="cd:us:4h:half_hour",
        kwargs={"interval": "4h", "market_filter": "us"}, **common,
    )

    # 1d: RTH 16:00 ET closing auction 后 daily bar 即定稿(SIP daily 不含盘后),
    # 留 30 min buffer (>20 min SIP free tier 延迟) → 主跑 16:30 ET
    # 兜底再跑一次 20:30 ET, 防 16:30 那次因网络/circuit 失败漏扫
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="30", timezone=et),
        id="cd:us:1d",
        kwargs={"interval": "1d", "market_filter": "us"},
        **common,
    )
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="20", minute="30", timezone=et),
        id="cd:us:1d:fallback",
        kwargs={"interval": "1d", "market_filter": "us"},
        **common,
    )
    # 5m: is_signal=False, 不扫信号; 但 watchlist 美股 K 线需要每日补齐, 否则
    # 详情页 5m chart 永远缺末几根。每 15 min 拉一次 (Alpaca SIP end_safe=now-20min,
    # 跑得太密没意义), 加 20:30 收尾扫盘后末根
    if kline is not None:
        sched.add_job(
            fetch_intraday_job,
            CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/15", timezone=et),
            args=(kline, watchlist),
            kwargs={"interval": "5m", "market_filter": "us"},
            id="fetch:us:5m",
            max_instances=1, coalesce=True, misfire_grace_time=300,
        )
        sched.add_job(
            fetch_intraday_job,
            CronTrigger(day_of_week="mon-fri", hour=20, minute=30, timezone=et),
            args=(kline, watchlist),
            kwargs={"interval": "5m", "market_filter": "us"},
            id="fetch:us:5m:close",
            max_instances=1, coalesce=True, misfire_grace_time=300,
        )
    log.info("scheduler.us_signal_jobs_attached")
