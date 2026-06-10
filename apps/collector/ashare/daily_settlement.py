"""A 股收盘结算 (事件/完成度驱动, 非 cron)。

根因 (2026-06-08 坐实): 拆分架构下 A 股日线 (1d) 无任何运行时写入路径 ——
旧单进程的 cd:1d 15:30 cron 在 ashare/main.py 没注册, 信号改事件驱动只读消费
(不 fetch)。1d 只在启动 startup_reconcile 写一次, 之后全天不更新 → 启动那次撞
SSL 抖动失败就卡旧数据, 不重启永不自愈。

本模块补上 1d 唯一的稳态写入路径, 且用"完成度驱动"门控:
- 检测交易 session open→closed 边沿 (用最后一根 5m close=15:00 收线作为"今天收盘"
  的天然信号, 不引入 cron)。
- 收盘后对每个标的拉当日 1d → 校验最新 ts 覆盖今日 (BJT) → 入库 → resample 1wk/1mo。
- sina 日线定稿有延迟 (旧 cron 故意等 15:30): 用条件等待 —— 未覆盖今日=未定稿,
  下一轮重试, 直到全部就位或超 deadline (容停牌/退市标的不会卡死)。
- 全部就位 = 当日数据齐全 → 本交易日不再结算 (转空闲), 进程不退出。

参考: docs/superpowers/skills/systematic-debugging/condition-based-waiting.md
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_after_market_close

log = structlog.get_logger(__name__)

_BJT = ZoneInfo("Asia/Shanghai")

# 结算轮询节拍 + 单次定稿等待上限。sina 收盘后日线定稿通常 <15min; 给到 45min
# deadline 足够, 超时仍未定稿的标的 (停牌/退市) 留到次日, 不无限重试拖死本轮。
_SETTLE_POLL_S = float(os.getenv("ASHARE_SETTLE_POLL_S", "60"))
_SETTLE_DEADLINE_S = float(os.getenv("ASHARE_SETTLE_DEADLINE_S", "2700"))
# 每标的间节流, 摊平 burst (与 startup_reconcile THROTTLE_S 一致量级)
_THROTTLE_S = float(os.getenv("ASHARE_SETTLE_THROTTLE_S", "1.0"))

# resample 派生周期窗口 (与 aggregate_derived 事件路径一致量级)
_AGG_KW = dict(window_1wk=14, window_1mo=40)


def _bjt_today(now: datetime | None = None) -> "datetime.date":
    return (now or datetime.now(timezone.utc)).astimezone(_BJT).date()


def _bar_covers_today(bar_ts: datetime, today) -> bool:
    """1d bar 是否就是今日这根 (雷区3: ts=UTC(D-1)16:00, 换 BJT 得交易日)。"""
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=timezone.utc)
    return bar_ts.astimezone(_BJT).date() >= today


async def settle_one(kline, repo, market: str, symbol: str, today) -> bool:
    """结算单标的当日 1d: 拉 → 校验覆盖今日 → resample 派生。

    返回 True=当日 1d 已就位 (定稿且入库); False=未定稿 (留待下一轮重试) 或失败。
    fetch_fresh_bars 内部已写库 (UNIQUE 幂等), 这里只判定与触发派生聚合。
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=2400)  # 深窗口, 与 startup_reconcile 一致
    try:
        bars = await kline.fetch_fresh_bars(symbol, interval="1d", start=start, end=now)
    except Exception as e:  # noqa: BLE001
        log.warning("settle.daily_fetch_failed", market=market, symbol=symbol, error=str(e))
        return False
    if not bars:
        return False
    if not _bar_covers_today(bars[-1].ts, today):
        # 未定稿: sina 收盘后日线还没出今日这根 → 留待下一轮
        return False
    # 1d 已就位 → resample 1wk/1mo (其余 intraday 派生由 5m 收线事件路径管)
    try:
        await aggregate_derived_for_symbol(repo, market, symbol, **_AGG_KW)
    except Exception as e:  # noqa: BLE001
        log.warning("settle.resample_failed", market=market, symbol=symbol, error=str(e))
    return True


async def run_settlement_round(kline, repo, symbols: list[str], today) -> set[str]:
    """对未就位标的跑一轮结算, 返回本轮已就位的标的集。

    单标的失败/未定稿不阻塞整批 (优雅降级)。节流摊平 burst。
    """
    settled: set[str] = set()
    for sym in symbols:
        try:
            if await settle_one(kline, repo, "ashare", sym, today):
                settled.add(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("settle.symbol_failed", symbol=sym, error=str(e))
        await asyncio.sleep(_THROTTLE_S)
    return settled


async def run_daily_settlement(
    kline, repo, symbols_provider, *, poll_s: float | None = None,
) -> None:
    """A 股收盘结算长驻任务 (完成度驱动门控, 非 cron)。

    主循环检测交易 session open→closed 边沿: 收盘瞬间触发一次结算, 条件等待
    (轮询重试) 直到当日所有标的 1d 就位或超 deadline, 然后本交易日转空闲。

    symbols_provider: async callable → list[str] (CORE ∪ watchlist, 每日收盘时取最新)。
    进程不退出: 跨交易日复用, 每天收盘各结算一次。
    """
    poll_s = poll_s or _SETTLE_POLL_S
    was_closed = is_after_market_close("ashare")
    settled_date = None  # 已完成结算的交易日, 防同日重复
    log.info("daily_settlement.start", market="ashare",
             poll_s=poll_s, deadline_s=_SETTLE_DEADLINE_S)

    while True:
        try:
            await asyncio.sleep(poll_s)
            now = datetime.now(timezone.utc)
            after_close = is_after_market_close("ashare")
            today = _bjt_today(now)

            # 边沿检测: 未过收盘→已过当日最后收盘(A股15:00) 才触发结算。
            # 用 is_after_market_close 而非 session_open: 后者午休(11:30-13:00)也=False,
            # 会把午休误当收盘触发 → sina 日线未定稿全失败 + 占用当日名额 → 真收盘不再结算。
            just_closed = (not was_closed) and after_close
            was_closed = after_close
            if not just_closed:
                continue
            if not is_trading_day("ashare") or settled_date == today:
                continue

            log.info("settlement.triggered", date=str(today))
            try:
                symbols = await symbols_provider()
            except Exception as e:  # noqa: BLE001
                log.warning("settlement.symbols_failed", error=str(e))
                continue

            # 条件等待: 反复结算未就位标的, 直到全就位或超 deadline
            pending = set(symbols)
            deadline = now.timestamp() + _SETTLE_DEADLINE_S
            rounds = 0
            while pending and datetime.now(timezone.utc).timestamp() < deadline:
                rounds += 1
                settled = await run_settlement_round(kline, repo, sorted(pending), today)
                pending -= settled
                log.info("settlement.round_done", date=str(today), round=rounds,
                         settled=len(settled), pending=len(pending))
                if pending:
                    await asyncio.sleep(poll_s)
            settled_date = today
            if pending:
                log.warning("settlement.deadline_pending", date=str(today),
                            pending=len(pending), note="留待次日 / 启动 reconcile 兜底")
            else:
                log.info("settlement.done", date=str(today), total=len(symbols))
        except asyncio.CancelledError:
            log.info("daily_settlement.cancelled")
            return
        except Exception as e:  # noqa: BLE001
            log.warning("daily_settlement.loop_error", error=str(e))
