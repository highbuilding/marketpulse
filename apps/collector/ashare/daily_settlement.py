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
import json
import os
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_and_publish, aggregate_derived_for_symbol
from core.cache import keys
from core.cache.redis_bars_cache import RedisBarsCache
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import expected_bar_ts, is_after_market_close

log = structlog.get_logger(__name__)

_BJT = ZoneInfo("Asia/Shanghai")

# 结算轮询节拍 + 单次定稿等待上限。sina 收盘后日线定稿通常 <15min; 给到 45min
# deadline 足够, 超时仍未定稿的标的 (停牌/退市) 留到次日, 不无限重试拖死本轮。
_SETTLE_POLL_S = float(os.getenv("ASHARE_SETTLE_POLL_S", "60"))
_SETTLE_DEADLINE_S = float(os.getenv("ASHARE_SETTLE_DEADLINE_S", "2700"))
# 每标的间节流, 摊平 burst (与 startup_reconcile THROTTLE_S 一致量级)
_THROTTLE_S = float(os.getenv("ASHARE_SETTLE_THROTTLE_S", "1.5"))

# resample 派生周期窗口 (与 aggregate_derived 事件路径一致量级)
_AGG_KW = dict(window_1wk=14, window_1mo=40)


def _bjt_today(now: datetime | None = None) -> "datetime.date":
    return (now or datetime.now(timezone.utc)).astimezone(_BJT).date()


def _bar_covers_today(bar_ts: datetime, today) -> bool:
    """1d bar 是否就是今日这根 (雷区3: ts=UTC(D-1)16:00, 换 BJT 得交易日)。"""
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=timezone.utc)
    return bar_ts.astimezone(_BJT).date() >= today


async def settle_one(kline, repo, market: str, symbol: str, today, *, redis_cache=None) -> bool:
    """结算单标的当日 1d: 拉 → 校验覆盖今日 → resample 派生 → 定稿 Redis current。

    返回 True=当日 1d 已就位 (定稿且入库); False=未定稿 (留待下一轮重试) 或失败。
    fetch_fresh_bars 内部已写库 (UNIQUE 幂等), 这里只判定与触发派生聚合。

    redis_cache 非空时, 1d 就位后用真实收盘根 (final=true) 覆盖 Redis
    cache_bars_current(1d): 收盘前 bar_poller 写的是 final=false provisional (用盘中
    残缺 5m 末根价合成, 可能偏离真实收盘价), 不定稿会残留 24h (TTL) 被 SSE init 推给
    前端盖掉权威收线根 → 大盘指数显示旧值。雷区3: current 的 ts 必须复用定稿根自身 ts。
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
    # 补齐当日尾盘 5m 缺根 (含 15:00 集合竞价根): bar_poller 在 15:00 session 一关即停摆,
    # 收盘段最后几根 5m 永久采不到 → 日线 provisional 用残缺价 + 5m K线尾根缺失。
    # 这里幂等补齐, 失败不影响 1d 就位判定 (优雅降级)。
    try:
        await _settle_tail_5m(kline, repo, market, symbol, today, redis_cache=redis_cache)
    except Exception as e:  # noqa: BLE001
        log.warning("settle.tail_5m_failed", market=market, symbol=symbol, error=str(e))
    # 定稿 Redis 1d current: 真实收盘根覆盖盘中 provisional (止血 stale 覆盖)
    if redis_cache is not None:
        await _finalize_daily_current(redis_cache, market, symbol, bars[-1])
    return True


async def _settle_tail_5m(kline, repo, market: str, symbol: str, today, *, redis_cache=None) -> None:
    """补齐当日 5m 到 15:00 集合竞价根, 并触发 intraday 派生聚合。

    fetch_fresh_bars(5m) 直拉 sina 整日 5m → 幂等写库, 自动补上 bar_poller 收盘瞬间
    停摆漏掉的尾盘根。用 expected_bar_ts 校验完成度仅作日志 (不阻塞: sina 收盘后 5m
    通常已齐, 罕见延迟下次 settlement round 再补)。
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    bjt_start = datetime(today.year, today.month, today.day, tzinfo=_BJT)
    start = bjt_start.astimezone(timezone.utc)
    fresh = await kline.fetch_fresh_bars(symbol, interval="5m", start=start, end=now)
    if not fresh:
        return
    expected = expected_bar_ts(market, today, 5)
    last_expected = expected[-1] if expected else None
    have_last = any(b.ts == last_expected for b in fresh) if last_expected else False
    log.info("settle.tail_5m", market=market, symbol=symbol, date=str(today),
             got=len(fresh), expected=len(expected), close_bar_ok=have_last)
    # 尾盘补齐后触发 intraday 派生 (15m/30m/60m/4h) 收尾根
    if redis_cache is not None:
        try:
            await aggregate_and_publish(
                repo, redis_cache, market, symbol,
                targets=("15m", "30m", "60m", "4h"), now=now)
        except Exception as e:  # noqa: BLE001
            log.warning("settle.tail_5m_agg_failed", market=market, symbol=symbol, error=str(e))


async def _finalize_daily_current(redis_cache, market: str, symbol: str, bar) -> None:
    """用真实收盘根 (final=true) 覆盖 Redis 1d current + 发 bus, 对称 bar_poller
    _publish_daily_provisional。redis 不可达仅 warning, 不拖死结算 (优雅降级)。"""
    payload = {
        "market": market, "symbol": symbol,
        "interval": "1d", "ts": bar.ts.isoformat(),
        "open": float(bar.open), "high": float(bar.high),
        "low": float(bar.low), "close": float(bar.close),
        "volume": int(bar.volume), "final": True,
    }
    try:
        await RedisBarsCache(redis_cache).upsert_tail(market, symbol, "1d", [bar])
        await redis_cache.set_msgpack(
            keys.cache_bars_current(market, symbol, "1d"), payload, ttl_s=86400)
        await redis_cache._r.xadd(  # noqa: SLF001
            keys.BUS_BARS_UPDATED,
            {"data": json.dumps(payload).encode()},
            maxlen=10000, approximate=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("settle.finalize_current_failed",
                    market=market, symbol=symbol, error=str(e))


async def run_settlement_round(kline, repo, symbols: list[str], today, *, redis_cache=None) -> set[str]:
    """对未就位标的跑一轮结算, 返回本轮已就位的标的集。

    单标的失败/未定稿不阻塞整批 (优雅降级)。节流摊平 burst。
    """
    settled: set[str] = set()
    for sym in symbols:
        try:
            if await settle_one(kline, repo, "ashare", sym, today, redis_cache=redis_cache):
                settled.add(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("settle.symbol_failed", symbol=sym, error=str(e))
        await asyncio.sleep(_THROTTLE_S * random.uniform(0.8, 1.3))
    return settled


async def run_daily_settlement(
    kline, repo, symbols_provider, *, poll_s: float | None = None, redis_cache=None,
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
            try:
                last_1d = repo.fetch_last_ts_map("ashare", "1d", symbols, closed_only=True)
            except Exception as e:  # noqa: BLE001
                log.warning("settlement.last_ts_failed", error=str(e))
                last_1d = {}
            pending = {
                s for s in symbols
                if not (last_1d.get(s) is not None and _bar_covers_today(last_1d[s], today))
            }
            log.info("settlement.scope", date=str(today), total=len(symbols),
                     already_settled=len(symbols) - len(pending), pending=len(pending),
                     throttle_s=_THROTTLE_S)
            deadline = now.timestamp() + _SETTLE_DEADLINE_S
            rounds = 0
            while pending and datetime.now(timezone.utc).timestamp() < deadline:
                rounds += 1
                settled = await run_settlement_round(
                    kline, repo, sorted(pending), today, redis_cache=redis_cache)
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
