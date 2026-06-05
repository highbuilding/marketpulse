"""启动 reconcile: collector 开机对核心+watchlist 标的回补缺口(gap 检测)。

冷启动填历史、kill 后补断档。**先比对 DB 末点,只补真缺口**——warm restart
数据新鲜时几乎零外部调用,避免 burst 打爆 sina/Alpaca 熔断器(审计 P0 复盘)。

种子语义(2026-06-05 用户明确):
- **逐周期源头直拉**,各周期有多少拉多少(5m/15m/30m/60m/4h 拿不到就跳过)。
- **聚合兜底仅限周线/月线**:1wk/1mo 源头直拉失败时,才从 1d 聚合。
- 4h 及以下源头拿不到 → 不聚合(种子环节不做不必要聚合)。
- 1d 走 kline.fetch_fresh_bars(深窗口 2400 天,覆盖 2020)。

失败单标的/单周期 try/except, 不阻塞启动。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol

log = structlog.get_logger(__name__)

# 直取分钟周期(源头直拉,不聚合)
_DIRECT_INTRADAY = ("5m", "15m", "30m")
# adapter.fetch_history_tf 直拉的非日线周期(60m/4h/1wk/1mo)
_TF_DIRECT = ("60m", "4h", "1wk", "1mo")
# 仅这两个周期允许"直拉失败→从日线聚合"兜底
_AGG_FALLBACK_OK = ("1wk", "1mo")

_DAILY_LOOKBACK_DAYS = 2400           # 覆盖到 2020-01(2347 天)。日线 + 周/月直拉窗口
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
        repo:    BarRepo(fetch_last_ts_map 比对缺口 + insert_bars 写直拉结果 + 传给聚合)
        kline:   KLineService(fetch_fresh_bars 拉 1d/分钟; _adapter_for 拿 adapter 直拉)
        symbols: 待回补标的(核心 + watchlist)
    """
    now = now or datetime.now(timezone.utc)
    start_deep = now - timedelta(days=_DAILY_LOOKBACK_DAYS)
    log.info("startup_reconcile.start", market=market, symbols=len(symbols))
    try:
        last_1d = repo.fetch_last_ts_map(market, "1d", symbols)
        last_5m = repo.fetch_last_ts_map(market, "5m", symbols)
        last_tf = {iv: repo.fetch_last_ts_map(market, iv, symbols) for iv in _TF_DIRECT}
    except Exception as e:  # noqa: BLE001
        log.warning("startup_reconcile.last_ts_failed", market=market, error=str(e))
        last_1d, last_5m, last_tf = {}, {}, {iv: {} for iv in _TF_DIRECT}

    filled = 0
    for sym in symbols:
        did_fetch = False
        needs_agg: list[str] = []  # 直拉失败、且允许聚合兜底的周期(仅 1wk/1mo)
        try:
            # 1) 日线深窗口(冷启动填史到 2020 + 给周/月聚合兜底打底)
            if _stale(last_1d.get(sym), now, _DAILY_STALE):
                await kline.fetch_fresh_bars(
                    sym, interval="1d", start=start_deep, end=now)
                did_fetch = True
                await asyncio.sleep(THROTTLE_S)

            # 2) 直取分钟周期(5m/15m/30m, 源头直拉, 不聚合)
            if _stale(last_5m.get(sym), now, _INTRADAY_STALE):
                for iv in _DIRECT_INTRADAY:
                    try:
                        await kline.fetch_fresh_bars(
                            sym, interval=iv, start=now - timedelta(days=60), end=now)
                    except Exception as e:  # noqa: BLE001
                        log.warning("reconcile.intraday_failed",
                                    market=market, symbol=sym, interval=iv, error=str(e))
                    await asyncio.sleep(THROTTLE_S)
                did_fetch = True

            # 3) 非日线周期源头直拉(60m/4h/1wk/1mo): adapter.fetch_history_tf
            adapter = kline._adapter_for(sym)  # noqa: SLF001
            for iv in _TF_DIRECT:
                if not _stale(last_tf[iv].get(sym), now, _INTRADAY_STALE):
                    continue
                try:
                    bars = await adapter.fetch_history_tf(sym, iv, start_deep, now)
                    if bars:
                        repo.insert_bars(bars)
                        did_fetch = True
                        log.info("reconcile.tf_direct", market=market, symbol=sym,
                                 interval=iv, bars=len(bars))
                    elif iv in _AGG_FALLBACK_OK:
                        needs_agg.append(iv)  # 直拉空 → 周/月聚合兜底
                    await asyncio.sleep(THROTTLE_S)
                except Exception as e:  # noqa: BLE001
                    # 直拉失败: 周/月进聚合兜底; 60m/4h 拿不到就跳过(不聚合)
                    if iv in _AGG_FALLBACK_OK:
                        needs_agg.append(iv)
                    log.warning("reconcile.tf_direct_failed", market=market,
                                symbol=sym, interval=iv, error=str(e),
                                fallback="agg" if iv in _AGG_FALLBACK_OK else "skip")

            # 4) 聚合兜底: 仅对直拉失败的周/月(从已补的 1d 聚合), 全量初始化
            if needs_agg:
                kw = {f"window_{iv}": None for iv in needs_agg}
                await aggregate_derived_for_symbol(repo, market, sym, **kw)
                log.info("reconcile.agg_fallback", market=market, symbol=sym,
                         intervals=needs_agg)

            if did_fetch:
                filled += 1
        except Exception as e:  # noqa: BLE001
            log.warning("startup_reconcile.symbol_failed",
                        market=market, symbol=sym, error=str(e))
    log.info("startup_reconcile.done", market=market, filled=filled, total=len(symbols))
