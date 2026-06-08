"""启动 reconcile: collector 开机对核心+watchlist 标的回补缺口(gap 检测)。

冷启动填历史、kill 后补断档。**先比对 DB 末点,只补真缺口**——warm restart
数据新鲜时几乎零外部调用,避免 burst 打爆 sina/Alpaca 熔断器(审计 P0 复盘)。

种子语义(2026-06-06 用户最终定义,A股/美股统一——5m+1d 直取, 其余聚合):

  A股 (sina 分钟数据浅 + 限频; 60m 直拉仅当日 ~8 根):
    直取:  5m(60天) + 1d(多年)
    聚合:  15m/30m/60m/4h ← 5m;  1wk/1mo ← 1d
  美股 (Alpaca):
    直取:  5m(59天) + 1d(多年)
    聚合:  15m/30m/60m/4h ← 5m;  1wk/1mo ← 1d

**两市场共同根治点**: 1wk/1mo/60m/4h 一律聚合(富途口径单锚点), 绝不源头直拉。
  双写 bug 根因(已修两次):
    - 1wk/1mo: 美股直拉(Alpaca 周一锚点)与聚合(W-FRI 周五)双写 → 每周两根。
    - 60m/4h:  美股直拉(Alpaca UTC 整点切, 无视 09:30 开盘; 4h 盘前盘中混一根)
               与聚合(富途口径按交易时段切)双写 → 间隔错乱/锚点不符看盘习惯。
  聚合深度跟随源(5m ~59天 / 1d 多年), 锚点正确(富途口径), 与 live sweep 同路径。

聚合复用 live 路径同一个 aggregate_derived_for_symbol(单一锚点, 杜绝双写)。
1d 走 kline.fetch_fresh_bars(深窗口 2400 天, 覆盖 2020)。
失败单标的/单周期 try/except, 不阻塞启动。

注意: 本流程仅"种子/开机回补"。进程启动后的 live 采集(bar_poller 直取 5m +
收线触发 aggregate_and_publish, sweep_derived 每 30min 补聚合)是另一套路径, 不受此影响。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol

log = structlog.get_logger(__name__)

# ── 种子直取/聚合配置(按 market 分野)──
# direct_intraday: 走 kline.fetch_fresh_bars 的分钟周期(sina/Alpaca intraday)
# direct_tf:       走 adapter.fetch_history_tf 的深历史周期(仅美股 60m/4h 划算)
# agg_full:        种子末尾全量聚合的派生周期(= 未直取的派生周期)→ aggregate_derived_for_symbol kwargs
_SEED_PLAN: dict[str, dict] = {
    "ashare": {
        "direct_intraday": ("5m",),
        "direct_tf": (),
        "agg_full": dict(window_15m=None, window_30m=None, window_60m=None,
                         window_4h=None, window_1wk=None, window_1mo=None),
    },
    "us": {
        "direct_intraday": ("5m",),
        "direct_tf": (),              # 回退(2026-06-06): Alpaca 60m/4h 整点切桶,
                                      # 无视 09:30 开盘 → 锚点错(4h 盘前盘中混一根)。
                                      # 改为从 5m 聚合(富途口径, 与 A股统一)。详见下方注释。
        "agg_full": dict(window_15m=None, window_30m=None, window_60m=None,
                         window_4h=None, window_1wk=None, window_1mo=None),
    },
}
# 兜底(未知市场): 只直取 5m+1d, 其余全聚合
_SEED_DEFAULT = {
    "direct_intraday": ("5m",),
    "direct_tf": (),
    "agg_full": dict(window_15m=None, window_30m=None, window_60m=None,
                     window_4h=None, window_1wk=None, window_1mo=None),
}

_DAILY_LOOKBACK_DAYS = 2400           # 覆盖到 2020-01(2347 天)。日线深窗口(也喂周/月聚合)
_DAILY_STALE = timedelta(days=2)      # 日线落后超 2 天才补
_INTRADAY_STALE = timedelta(days=4)   # 分钟/TF 落后超 4 天才补(否则 live poller 自愈)
THROTTLE_S = 1.5   # 温和节流: 摊平 startup burst, 不加剧 sina/Alpaca 限频(P0 复盘)
# 失败日线标的重试前的等待: 给瞬时网络抖动平息 / breaker 5min open 窗口进入 half-open。
_DAILY_RETRY_DELAY_S = float(os.getenv("RECONCILE_DAILY_RETRY_DELAY_S", "20"))


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

    种子流程(A股/美股分野, 见模块 docstring): 直取该市场配置的周期(1d + 分钟 +
    可选 TF), 末尾对未直取的派生周期统一全量聚合。聚合复用 live 同一路径, 锚点唯一。

    Args:
        market:  市场标识 ("ashare" / "us")
        repo:    BarRepo(fetch_last_ts_map 比对缺口 + insert_bars 写直拉结果 + 传给聚合)
        kline:   KLineService(fetch_fresh_bars 拉 1d/分钟; _adapter_for 拿 adapter 直拉)
        symbols: 待回补标的(核心 + watchlist)
    """
    now = now or datetime.now(timezone.utc)
    start_deep = now - timedelta(days=_DAILY_LOOKBACK_DAYS)
    plan = _SEED_PLAN.get(market, _SEED_DEFAULT)
    direct_intraday = plan["direct_intraday"]
    direct_tf = plan["direct_tf"]
    agg_full = plan["agg_full"]
    log.info("startup_reconcile.start", market=market, symbols=len(symbols),
             direct_intraday=direct_intraday, direct_tf=direct_tf)
    try:
        last_1d = repo.fetch_last_ts_map(market, "1d", symbols)
        last_5m = repo.fetch_last_ts_map(market, "5m", symbols)
        last_tf = {iv: repo.fetch_last_ts_map(market, iv, symbols) for iv in direct_tf}
    except Exception as e:  # noqa: BLE001
        log.warning("startup_reconcile.last_ts_failed", market=market, error=str(e))
        last_1d, last_5m, last_tf = {}, {}, {iv: {} for iv in direct_tf}

    filled = 0
    failed_daily: list[str] = []  # 日线 fetch 失败的标的, 末尾重试一轮
    for sym in symbols:
        did_fetch = False
        try:
            # 1) 日线深窗口(冷启动填史到 2020 + 喂周/月聚合)
            #    独立 try/except: 日线瞬时失败(经代理 SSL 抖动等)不再冒泡中断
            #    本标的的分钟/聚合步。ak_call 已有网络瞬时重试; 这里再收集失败标的
            #    末尾补一轮, 双保险根治"冷启动撞抖动 → 一批标的卡旧数据到次日"。
            if _stale(last_1d.get(sym), now, _DAILY_STALE):
                try:
                    await kline.fetch_fresh_bars(
                        sym, interval="1d", start=start_deep, end=now)
                    did_fetch = True
                except Exception as e:  # noqa: BLE001
                    failed_daily.append(sym)
                    log.warning("reconcile.daily_failed",
                                market=market, symbol=sym, error=str(e))
                await asyncio.sleep(THROTTLE_S)

            # 2) 直取分钟周期(源头直拉, 不聚合): A股/美股都只 5m
            if _stale(last_5m.get(sym), now, _INTRADAY_STALE):
                for iv in direct_intraday:
                    try:
                        await kline.fetch_fresh_bars(
                            sym, interval=iv, start=now - timedelta(days=60), end=now)
                    except Exception as e:  # noqa: BLE001
                        log.warning("reconcile.intraday_failed",
                                    market=market, symbol=sym, interval=iv, error=str(e))
                    await asyncio.sleep(THROTTLE_S)
                did_fetch = True

            # 3) 深历史 TF 直拉(仅美股 60m/4h, Alpaca 6年; A股 direct_tf 为空跳过)
            if direct_tf:
                adapter = kline._adapter_for(sym)  # noqa: SLF001
                for iv in direct_tf:
                    if not _stale(last_tf[iv].get(sym), now, _INTRADAY_STALE):
                        continue
                    try:
                        bars = await adapter.fetch_history_tf(sym, iv, start_deep, now)
                        if bars:
                            repo.insert_bars(bars)
                            did_fetch = True
                            log.info("reconcile.tf_direct", market=market, symbol=sym,
                                     interval=iv, bars=len(bars))
                        await asyncio.sleep(THROTTLE_S)
                    except Exception as e:  # noqa: BLE001
                        # 直拉失败就跳过(60m/4h 不做聚合兜底; 聚合只给 59 天没意义)
                        log.warning("reconcile.tf_direct_failed", market=market,
                                    symbol=sym, interval=iv, error=str(e), fallback="skip")

            # 4) 派生周期统一全量聚合(= 该市场未直取的周期; 与 live sweep 同路径, 单锚点)
            #    A股: 15m/30m/60m/4h ← 5m, 1wk/1mo ← 1d
            #    美股: 15m/30m ← 5m, 1wk/1mo ← 1d (60m/4h 已直取)
            try:
                await aggregate_derived_for_symbol(repo, market, sym, **agg_full)
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile.agg_failed", market=market, symbol=sym, error=str(e))

            if did_fetch:
                filled += 1
        except Exception as e:  # noqa: BLE001
            log.warning("startup_reconcile.symbol_failed",
                        market=market, symbol=sym, error=str(e))

    # 失败日线标的补一轮: 冷启动瞬时抖动(经代理 SSL EOF)+ breaker 短暂 open 时,
    # 首轮可能整批失败。sleep 让抖动平息 / breaker 进入 half-open, 再重拉一次。
    # ak_call 自身的网络瞬时重试管"单次调用内"的抖动; 这一轮管"breaker open 期间
    # 被直接拒绝"的标的(那种 ak_call 没机会重试)。仍失败的留给次日兜底, 不无限重试。
    if failed_daily:
        log.info("reconcile.daily_retry_start", market=market, count=len(failed_daily))
        await asyncio.sleep(_DAILY_RETRY_DELAY_S)
        recovered = 0
        for sym in failed_daily:
            try:
                await kline.fetch_fresh_bars(
                    sym, interval="1d", start=start_deep, end=now)
                # 补聚合派生周期(日线刚补上, 1wk/1mo 需要重算)
                try:
                    await aggregate_derived_for_symbol(repo, market, sym, **agg_full)
                except Exception as e:  # noqa: BLE001
                    log.warning("reconcile.retry_agg_failed",
                                market=market, symbol=sym, error=str(e))
                recovered += 1
                filled += 1
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile.daily_retry_failed",
                            market=market, symbol=sym, error=str(e))
            await asyncio.sleep(THROTTLE_S)
        log.info("reconcile.daily_retry_done", market=market,
                 recovered=recovered, total=len(failed_daily))

    log.info("startup_reconcile.done", market=market, filled=filled, total=len(symbols))
