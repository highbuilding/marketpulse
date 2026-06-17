"""A 股数据冲刷命令: 清洗 (删除) 脏数据并从源头重新拉取写入。

历史数据出问题时 (前复权错乱 / 价格跳变 / 时间戳错 / 残缺进行态根残留) 用本命令
按标的+周期+时间窗删除后重拉。复用 collector 的 ak middleware + worker 池 + KLineService。

雷区6: 持 DuckDB RW 连接, 必须在 collector-ashare 停止后单独跑 (否则撞 RW 锁)。
        盘中也不要跑 (与采集抢源)。建议收盘后停 collector → 冲刷 → 重启。

用法 (项目根, venv 激活):
    # 冲刷单个标的的 1d (删全部历史再重拉)
    python -m apps.collector.ashare.flush --symbols 603986.SH --intervals 1d

    # 冲刷多个标的的 5m+1d 指定时间窗
    python -m apps.collector.ashare.flush --symbols 603986.SH,000001.SH \\
        --intervals 5m,1d --start 2026-01-01 --end 2026-06-16

    # 冲刷整个采集清单 (谨慎: 耗时长) 的 1d
    python -m apps.collector.ashare.flush --all --intervals 1d

    # 只删不重拉 (清掉脏数据等下次 reconcile 补)
    python -m apps.collector.ashare.flush --symbols 603986.SH --intervals 5m --delete-only

    # 冲刷 K线 + 同时重扫 CD 信号 (删旧信号, 基于干净 bar 重生成时间准确信号)
    python -m apps.collector.ashare.flush --symbols 603986.SH --intervals 5m,1d --signals

    # 只冲刷 CD 信号, 不动 K线 (历史信号时间错乱但 K线正确时用)
    python -m apps.collector.ashare.flush --symbols 603986.SH --signals-only
    python -m apps.collector.ashare.flush --all --signals-only   # 全清单重扫 CD
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

_BASE = Path(__file__).resolve().parents[3]
_DATA = _BASE / "data"

log = structlog.get_logger(__name__)

_DERIVED_FROM_5M = ("15m", "30m", "60m", "4h")
_DERIVED_FROM_1D = ("1wk", "1mo")
# CD 信号扫描的周期 (与 signal_scan_consumer 消息驱动口径一致: A 股 CD 信号
# 已无 cron, 由 5m 收线事件→bus:bars.updated→consumer 只读已入库收线 bar 计算)
_CD_INTERVALS = ("15m", "30m", "60m", "4h", "1d")


async def _bootstrap():
    """复用 collector 的 middleware + worker 池 + KLineService + BarRepo。"""
    from apps.collector.base import setup_proxy_and_logging
    setup_proxy_and_logging("collector_ashare_flush", use_proxy=False)

    from core.cache.redis_client import make_redis
    from core.cache.redis_client import RedisCache
    from core.integrations import ak_middleware
    from core.integrations.breaker import SourceBreaker
    from core.integrations.outlets import LocalOutlet, OutletPool
    from core.integrations.ratelimit import RedisTokenBucket
    from core.integrations.akshare import init_worker_pool
    from core.persistence.duckdb_repo import BarRepo
    from apps.api.deps import set_bar_repo_override, get_kline_service

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    redis_raw = make_redis(redis_url)
    redis_cache = RedisCache(redis_raw)
    outlet_pool = OutletPool([LocalOutlet()], cache=redis_cache, cooling_seconds=1800)
    breakers = {s: SourceBreaker(source=s, cache=redis_cache) for s in ("sina", "em", "ths")}
    ratelimits = {
        "sina": RedisTokenBucket(redis=redis_raw, key="ratelimit:source:sina", rate=5, burst=20),
        "em": RedisTokenBucket(redis=redis_raw, key="ratelimit:source:em", rate=10, burst=50),
        "ths": RedisTokenBucket(redis=redis_raw, key="ratelimit:source:ths", rate=3, burst=10),
    }
    ak_middleware.setup(ak_middleware.AkMiddleware(
        outlet_pool=outlet_pool, breakers=breakers, ratelimits=ratelimits,
    ))
    await init_worker_pool(int(os.getenv("AK_WORKER_POOL_SIZE", "4")))

    bar_repo = BarRepo(str(_DATA / "bars_ashare.duckdb"))
    bar_repo.init()
    set_bar_repo_override(bar_repo)
    kline = get_kline_service()

    # CD 信号重扫服务 (删旧 + 基于干净 bar 重生成准确信号 + 同步重建带名字的消息)
    from core.persistence.signal_repo import SignalRepo
    from core.persistence.live_message_repo import LiveMessageRepo
    from core.services.signal_service import SignalScanService
    from apps.api.deps import get_live_message_service
    signal_repo = SignalRepo(str(_DATA / "state.db"))
    live_msg_repo = LiveMessageRepo(str(_DATA / "state.db"))
    scan = SignalScanService(
        kline, signal_repo,
        live_message_repo=live_msg_repo,
        live_message_service=get_live_message_service(),
    )
    return bar_repo, kline, scan


async def flush_symbol(
    bar_repo, kline, symbol: str, intervals: list[str],
    *, start: datetime, end: datetime, delete_only: bool,
) -> None:
    for interval in intervals:
        deleted = bar_repo.delete_bars(
            "ashare", symbol, interval, start=start, end=end)
        log.info("flush.deleted", symbol=symbol, interval=interval, rows=deleted)
        if delete_only:
            continue
        # 派生周期 (15m/30m/60m/4h/1wk/1mo) 不直接重拉, 由 5m/1d 重拉后聚合
        if interval in (*_DERIVED_FROM_5M, *_DERIVED_FROM_1D):
            continue
        try:
            bars = await kline.fetch_fresh_bars(symbol, interval=interval, start=start, end=end)
            log.info("flush.refetched", symbol=symbol, interval=interval, bars=len(bars))
        except Exception as e:  # noqa: BLE001
            log.warning("flush.refetch_failed", symbol=symbol, interval=interval, error=str(e))


async def reaggregate(bar_repo, symbol: str, intervals: list[str]) -> None:
    """重拉 5m/1d 后, 重新聚合受影响的派生周期。"""
    from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol
    kw: dict = {}
    if "5m" in intervals:
        kw |= dict(window_15m=None, window_30m=None, window_60m=None, window_4h=None)
    if "1d" in intervals:
        kw |= dict(window_1wk=None, window_1mo=None)
    if not kw:
        return
    try:
        await aggregate_derived_for_symbol(bar_repo, "ashare", symbol, **kw)
        log.info("flush.reaggregated", symbol=symbol, targets=list(kw.keys()))
    except Exception as e:  # noqa: BLE001
        log.warning("flush.reaggregate_failed", symbol=symbol, error=str(e))


async def _main() -> None:
    parser = argparse.ArgumentParser(description="A 股数据冲刷 (删脏数据 + 重拉)")
    parser.add_argument("--symbols", default="", help="逗号分隔标的, 如 603986.SH,000001.SH")
    parser.add_argument("--all", action="store_true", help="冲刷整个采集清单 (collector_symbols)")
    parser.add_argument("--intervals", default="1d", help="逗号分隔周期, 如 5m,1d (派生由聚合重建)")
    parser.add_argument("--start", default=None, help="起始日 YYYY-MM-DD, 缺省=2019(深窗口)")
    parser.add_argument("--end", default=None, help="结束日 YYYY-MM-DD, 缺省=今天")
    parser.add_argument("--delete-only", action="store_true", help="只删不重拉 K线")
    parser.add_argument("--signals", action="store_true",
                        help="同时冲刷 CD 信号: 删旧 + 基于干净 bar 重扫 (时间准确)")
    parser.add_argument("--signals-only", action="store_true",
                        help="只冲刷 CD 信号, 不动 K线 (历史信号有问题但 K线正确时用)")
    parser.add_argument("--throttle", type=float, default=1.5, help="标的间节流秒数")
    args = parser.parse_args()

    intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
    now = datetime.now(timezone.utc)
    start = (datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
             if args.start else now - timedelta(days=2400))
    # end 含当天全天 (日期边界 +1 天), 否则当天盘中 bar (UTC 01:30~07:00) 会被排除
    end = (datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) + timedelta(days=1)
           if args.end else now)

    bar_repo, kline, scan = await _bootstrap()

    if args.all:
        from apps.api.deps import get_collector_symbol_repo
        from core.domain.markets import infer_market
        symbols = sorted({
            s for s in await get_collector_symbol_repo().active_symbols("ashare", capability="5m")
            if infer_market(s) == "ashare"
        })
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        log.warning("flush.no_symbols", note="需 --symbols 或 --all")
        return

    do_signals = args.signals or args.signals_only
    # CD 信号扫描周期 = 请求周期 ∩ CD 支持周期 (signals-only 时用全部 CD 周期)
    cd_intervals = (list(_CD_INTERVALS) if args.signals_only
                    else [iv for iv in intervals if iv in _CD_INTERVALS])

    log.info("flush.start", symbols=len(symbols), intervals=intervals,
             start=start.date().isoformat(), end=end.date().isoformat(),
             delete_only=args.delete_only, signals=do_signals,
             signals_only=args.signals_only)
    for i, sym in enumerate(symbols):
        if not args.signals_only:
            await flush_symbol(bar_repo, kline, sym, intervals,
                               start=start, end=end, delete_only=args.delete_only)
            if not args.delete_only:
                await reaggregate(bar_repo, sym, intervals)
        # CD 信号冲刷: 删旧 + 基于 (已冲刷的) 干净 bar 重扫
        if do_signals:
            for iv in cd_intervals:
                try:
                    deleted, written = await scan.rescan_clean(sym, iv)
                    log.info("flush.cd_rescan", symbol=sym, interval=iv,
                             deleted=deleted, written=written)
                except Exception as e:  # noqa: BLE001
                    log.warning("flush.cd_rescan_failed", symbol=sym, interval=iv, error=str(e))
        if i < len(symbols) - 1:
            await asyncio.sleep(args.throttle)
    log.info("flush.done", symbols=len(symbols))

    from core.integrations.akshare import close_worker_pool
    try:
        await close_worker_pool()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    asyncio.run(_main())
