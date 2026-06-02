"""启动 reconcile: collector 开机对核心+watchlist 标的回补缺口(gap 检测)。

冷启动填历史、kill 后补断档。**先比对 DB 末点,只补真缺口**——warm restart
数据新鲜时几乎零外部调用,避免 burst 打爆 sina/Alpaca 熔断器(审计 P0 复盘)。
先 1d 再聚合派生(1wk/1mo 依赖 1d 先到位)。失败单标的 try/except, 不阻塞启动。

intraday(5m/15m/30m)只在"陈旧超过 live poller 自愈窗口"时才补——日常近端
缺口由 live bar_poller / cron 拉当日全序列自然补齐,reconcile 不重复抢源。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol

log = structlog.get_logger(__name__)

_DIRECT_INTRADAY = ("5m", "15m", "30m")
_DAILY_LOOKBACK_DAYS = 1000
_DAILY_STALE = timedelta(days=2)      # 日线落后超 2 天才补
_INTRADAY_STALE = timedelta(days=4)   # intraday 落后超 4 天才补(否则 live poller 自愈)
THROTTLE_S = 1.5   # 温和节流: 摊平 startup burst, 不加剧 sina/Alpaca 限频(P0 复盘)


def _stale(last: datetime | None, now: datetime, thresh: timedelta) -> bool:
    """DB 末点缺失或落后超阈值 → 视为需回补。兼容 naive(UTC)/aware 时间戳。"""
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) > thresh


async def run_startup_reconcile(
    market: str, repo, kline, symbols: list[str], *, now: datetime | None = None,
) -> None:
    """collector 开机回补入口。

    Args:
        market:  市场标识 ("ashare" / "us")
        repo:    BarRepo(fetch_last_ts_map 比对缺口 + 传给聚合)
        kline:   KLineService(fetch_fresh_bars)
        symbols: 待回补标的(核心 + watchlist)
    """
    now = now or datetime.now(timezone.utc)
    log.info("startup_reconcile.start", market=market, symbols=len(symbols))
    try:
        last_1d = repo.fetch_last_ts_map(market, "1d", symbols)
        last_5m = repo.fetch_last_ts_map(market, "5m", symbols)
    except Exception as e:  # noqa: BLE001
        log.warning("startup_reconcile.last_ts_failed", market=market, error=str(e))
        last_1d, last_5m = {}, {}

    filled = 0
    for sym in symbols:
        did_fetch = False
        try:
            # 1) 日线缺口 → 深窗口补(冷启动填史 + 给 1wk/1mo 聚合打底)
            if _stale(last_1d.get(sym), now, _DAILY_STALE):
                await kline.fetch_fresh_bars(
                    sym, interval="1d",
                    start=now - timedelta(days=_DAILY_LOOKBACK_DAYS), end=now)
                did_fetch = True
                await asyncio.sleep(THROTTLE_S)
            # 2) intraday 缺口超自愈窗口 → 补直取周期(近端缺口交给 live poller)
            if _stale(last_5m.get(sym), now, _INTRADAY_STALE):
                for iv in _DIRECT_INTRADAY:
                    await kline.fetch_fresh_bars(
                        sym, interval=iv, start=now - timedelta(days=60), end=now)
                    await asyncio.sleep(THROTTLE_S)
                did_fetch = True
            # 3) 仅当确实补了 base 才重聚合派生(否则派生已随 base 当前, 跳过省算)
            if did_fetch:
                await aggregate_derived_for_symbol(
                    repo, market, sym,
                    window_60m=None, window_4h=None, window_1wk=None, window_1mo=None)
                filled += 1
        except Exception as e:  # noqa: BLE001
            log.warning("startup_reconcile.symbol_failed",
                        market=market, symbol=sym, error=str(e))
    log.info("startup_reconcile.done", market=market, filled=filled, total=len(symbols))
