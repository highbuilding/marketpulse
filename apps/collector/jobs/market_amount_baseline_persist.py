"""每日收盘后写当日 5m 累计成交额曲线 → SQLite baseline 表。

供 *_index_minute job 次日盘中查同时段 prev_day cum_amount 算 amount_ratio。

A 股: BJT 15:35 (收盘 15:00 + 30min 缓冲)
港股: BJT 16:05
美股: ET 16:05 (= BJT 04:05 / 05:05 冬夏令时跟随)

Crypto 不需要 (Binance 24h ticker 现成)。

参考: docs/superpowers/specs/2026-05-28-market-index-extended-design.md §5.3
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from core.integrations.akshare import ak_call
from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo

log = structlog.get_logger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")
_ET_TZ = ZoneInfo("America/New_York")

# A 股 INDEX 用于代表市场总量 (上证指数, 5m 桶累计 amount)
_ASHARE_REF_INDEX = "sh000001"


def _ashare_offset_from_dt(dt_bjt: datetime) -> int | None:
    """A 股 BJT 时刻 → 5m offset (0..47)。

    上午 9:30-11:30 = 0..23 (24 桶)
    下午 13:00-15:00 = 24..47 (24 桶)
    其他时段返回 None。
    """
    t = dt_bjt.time()
    h, m = t.hour, t.minute
    minutes_from_open = (h - 9) * 60 + (m - 30)  # 9:30 = 0
    if 0 <= minutes_from_open < 120:  # 上午段
        return minutes_from_open // 5
    minutes_from_pm = (h - 13) * 60 + m  # 13:00 = 0 of pm
    if 0 <= minutes_from_pm < 120:  # 下午段
        return 24 + minutes_from_pm // 5
    if h == 11 and m == 30:
        return 23
    if h == 15 and m == 0:
        return 47
    return None


async def persist_ashare_baseline(repo: MarketAmountBaselineRepo) -> int:
    """A 股: 拉上证 5m 序列, 累加 amount 写当日 0..47 桶。

    Returns: 写入行数 (0 = 失败 / 非交易日)
    """
    from core.domain.market_calendar import is_trading_day

    now_bjt = datetime.now(_CN_TZ)
    if not is_trading_day("ashare", now_bjt):
        log.debug("baseline.ashare.skip_non_trading_day", date=str(now_bjt.date()))
        return 0

    today = now_bjt.date().isoformat()
    try:
        df = await ak_call(
            "stock_zh_a_minute", symbol=_ASHARE_REF_INDEX, period="5", adjust="",
            caller="baseline_persist.ashare",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("baseline.ashare.fetch_failed", error=str(e))
        return 0

    points: list[tuple[int, float]] = []
    cum_amount = 0.0
    for _, row in df.iterrows():
        try:
            naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
            ts_bjt = naive.replace(tzinfo=_CN_TZ)
            if ts_bjt.date().isoformat() != today:
                continue  # 跳过非今日数据
            offset = _ashare_offset_from_dt(ts_bjt)
            if offset is None:
                continue
            cum_amount += float(row["amount"])
            points.append((offset, cum_amount))
        except Exception:  # noqa: BLE001
            continue

    if not points:
        log.warning("baseline.ashare.no_points_today", date=today)
        return 0

    n = await repo.upsert_day("ashare", today, points)
    log.info("baseline.ashare.persisted", date=today, rows=n,
             total_amount_yiyuan=cum_amount / 1e8)
    return n


async def persist_hk_baseline(repo: MarketAmountBaselineRepo) -> int:
    """港股: 复用 hk_index_minute 取的 sina spot amount, 但每 5m 桶级数据
    sina 港股不提供 5m kline。降级方案: 仅写最后一桶 (offset=46) 全日累计。

    这意味着港股 amount_ratio 对比口径是"昨日全日 vs 今日全日", 而不是"同时段进度"。
    实际进度比的精度受限。这是港股 sina 接口的固有限制。
    """
    from core.domain.market_calendar import is_trading_day

    now_bjt = datetime.now(_CN_TZ)
    if not is_trading_day("hk", now_bjt):
        log.debug("baseline.hk.skip_non_trading_day", date=str(now_bjt.date()))
        return 0
    today = now_bjt.date().isoformat()

    # 从港股 collector 已写的 cache 拿 amount (Plan B 实装后)
    # 暂时跳过, 等 Plan B 落地后填充
    log.info("baseline.hk.skipped_pending_plan_b", date=today)
    return 0


async def persist_us_baseline(repo: MarketAmountBaselineRepo) -> int:
    """美股: 拉 SPY/QQQ/DIA 当日 5m 桶, 累加 amount 写 0..77 桶 (6.5h × 12)。

    Plan C 实装后填充。
    """
    from core.domain.market_calendar import is_trading_day
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if not is_trading_day("us", now_et):
        log.debug("baseline.us.skip_non_trading_day", date=str(now_et.date()))
        return 0
    log.info("baseline.us.skipped_pending_plan_c")
    return 0


async def cleanup_old_baselines(repo: MarketAmountBaselineRepo, days: int = 20) -> int:
    """删除 N 天前数据 (默认 20 天)。"""
    deleted = await repo.cleanup_older_than(days=days)
    log.info("baseline.cleanup", deleted=deleted, retain_days=days)
    return deleted
