"""定期聚合派生周期 (60m/4h/1wk/1mo).

collector 日常运行时: WS/poll 写新的 5m/1d bars → DuckDB。
此 job 每 30 分钟扫一次, 对有时间窗口内有更新的标的重新聚合。
DuckDB upsert (ON CONFLICT) 自动去重, 重复聚合无副作用。

首次运行 / 历史缺失检测: 按目标周期独立判断 —— 源数据远早于目标数据
说明历史未完全聚合, 自动全量初始化该周期。后续运行走增量窗口。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import structlog

from core.cache import keys
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo
from core.services.intraday_aggregator import aggregate_intraday

log = structlog.get_logger(__name__)

# 增量聚合窗口
_AGG_WINDOW_DAYS = 1       # 60m/4h — 只补最近 1 天
_RESAMPLE_WINDOW_DAYS = 7  # 1wk/1mo — 只补最近 7 天

# 全量初始化起点 — 远早于任何市场的数据起始, DuckDB 自然只返回实际存在的行
_FULL_START = datetime(1970, 1, 1, tzinfo=timezone.utc)


async def _agg_one(
    repo: BarRepo, market: str, symbol: str,
    target_iv: str, source_iv: str, interval_minutes: int,
    window_days: int | None,
) -> int:
    """从 source_iv 聚合 target_iv.

    window_days=None: 全量初始化 (拉全部源数据).
    window_days=int:  增量模式 (只拉最近 N 天源数据, 窗口内聚合结果写库).
    """
    now = datetime.now(timezone.utc)
    if window_days is None:
        start = _FULL_START
    else:
        start = now - timedelta(days=window_days)
    raw = repo.fetch_history(market, symbol, start, now, interval=source_iv)
    if not raw:
        return 0
    agg = aggregate_intraday(raw, market, interval_minutes)  # type: ignore[arg-type]
    if not agg:
        return 0
    if window_days is not None:
        agg = [b for b in agg if b.ts >= start]
    if agg:
        repo.insert_bars(agg)
    return len(agg)


async def _resample_one(
    repo: BarRepo, market: str, symbol: str,
    target_iv: str, freq: str, window_days: int | None,
) -> int:
    """从 1d resample target_iv.

    window_days=None: 全量初始化 (拉全部 1d 数据).
    window_days=int:  增量模式 (只拉最近 N 天 1d, 窗口内结果写库).
    """
    import pandas as pd
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    if window_days is None:
        start = _FULL_START
    else:
        start = now - timedelta(days=window_days)
    daily = repo.fetch_history(market, symbol, start, now, interval="1d")
    if not daily:
        return 0

    df = pd.DataFrame([{
        "ts": b.ts, "o": float(b.open), "h": float(b.high),
        "l": float(b.low), "c": float(b.close), "v": b.volume,
    } for b in daily])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()

    resampled = df.resample(freq).agg({
        "o": "first", "h": "max", "l": "min", "c": "last", "v": "sum",
    }).dropna()

    bars = [
        Bar(
            market=market, symbol=symbol,
            ts=idx.to_pydatetime().replace(tzinfo=timezone.utc),
            open=Decimal(str(r.o)), high=Decimal(str(r.h)),
            low=Decimal(str(r.l)), close=Decimal(str(r.c)),
            volume=int(r.v), interval=target_iv,
        )
        for idx, r in resampled.iterrows()
    ]
    if window_days is not None:
        bars = [b for b in bars if b.ts >= start]
    if bars:
        repo.insert_bars(bars)
    return len(bars)


# ── 窗口决策 ──
# 每个目标周期有两种触发条件:
#   全量 (None): 从未有过目标数据, 或源数据最早点远早于目标最早点
#   增量 (N 天): 源数据有新的 bar 需要聚合
#   跳过 (sentinel _NOOP): 两种条件都不满足

_NOOP = "skip"  # sentinel: 此周期不需任何操作


def _decide_window(
    source_first: datetime | None,
    source_last: datetime | None,
    target_first: datetime | None,
    target_last: datetime | None,
    full_gap: timedelta,
    incr_window: int,
) -> int | None | str:
    """返回 None(全量), int(增量天数), 或 _NOOP(无需操作)."""
    if source_last is None:
        return _NOOP  # 没源数据

    # 全量检测: 目标从未存在, 或源数据远早于目标 (历史缺失)
    if target_last is None:
        return None
    if source_first is not None and target_first is not None:
        if source_first < target_first - full_gap:
            return None  # 历史缺口 → 全量

    # 增量检测: 源数据更新于目标数据
    if source_last > target_last + timedelta(hours=1):
        return incr_window

    return _NOOP


async def aggregate_derived_for_symbol(
    repo: BarRepo, market: str, symbol: str,
    *,
    window_15m: int | None | str = _NOOP,
    window_30m: int | None | str = _NOOP,
    window_60m: int | None | str = _NOOP,
    window_4h: int | None | str = _NOOP,
    window_1wk: int | None | str = _NOOP,
    window_1mo: int | None | str = _NOOP,
) -> dict:
    """对单个标的执行派生聚合. 各目标周期传入 None=全量, int=增量窗口, _NOOP=跳过."""
    stats: dict[str, int] = {}

    for target, source, mins, w in [
        ("15m", "5m", 15,  window_15m),
        ("30m", "5m", 30,  window_30m),
        ("60m", "5m", 60,  window_60m),
        ("4h",  "5m", 240, window_4h),
    ]:
        if w is _NOOP:
            continue
        window_days: int | None = None if w is None else int(w)
        try:
            n = await _agg_one(repo, market, symbol, target, source, mins,
                               window_days=window_days)
            stats[target] = n
        except Exception as e:
            log.warning("derived.agg_failed",
                        symbol=symbol, target=target, error=str(e))

    for target, freq, w in [
        ("1wk", "W-FRI", window_1wk),
        ("1mo", "ME",    window_1mo),
    ]:
        if w is _NOOP:
            continue
        window_days = None if w is None else int(w)
        try:
            n = await _resample_one(repo, market, symbol, target, freq,
                                    window_days=window_days)
            stats[target] = n
        except Exception as e:
            log.warning("derived.resample_failed",
                        symbol=symbol, target=target, error=str(e))

    return stats


# 各目标周期事件驱动聚合用的增量窗口(天)
_TARGET_WINDOW = {
    "15m": dict(window_15m=2), "30m": dict(window_30m=2),
    "60m": dict(window_60m=2), "4h": dict(window_4h=2),
    "1wk": dict(window_1wk=14), "1mo": dict(window_1mo=40),
}


async def aggregate_and_publish(
    repo, redis, market: str, symbol: str,
    *, targets: tuple[str, ...], now: datetime | None = None,
) -> None:
    """事件驱动: 聚合 targets 指定的周期, 对已收线(ts<=now)的最新桶发 bus(final=true)。

    未收线的桶(ts>now)交给进行中态组件 ticker, 这里不发。
    """
    now = now or datetime.now(timezone.utc)
    kw: dict = {}
    for t in targets:
        kw.update(_TARGET_WINDOW.get(t, {}))
    try:
        await aggregate_derived_for_symbol(repo, market, symbol, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("derived.publish_agg_failed", symbol=symbol, error=str(e))
        return
    for t in targets:
        try:
            latest = repo.fetch_history_paged(market, symbol, t, before=None, limit=1)
            if not latest:
                continue
            bar = latest[-1]
            if bar.ts > now:  # 未收线, 交给 ticker
                continue
            payload = {
                "market": market, "symbol": symbol, "interval": t,
                "ts": bar.ts.isoformat(), "open": float(bar.open),
                "high": float(bar.high), "low": float(bar.low),
                "close": float(bar.close), "volume": int(bar.volume),
                "final": True,
            }
            await redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("derived.publish_failed", symbol=symbol, target=t, error=str(e))


async def sweep_derived(
    repo: BarRepo, market: str, symbols: list[str],
) -> None:
    """扫描并补全需要更新的派生周期.

    每 30 分钟由 collector scheduler 调用.
    各目标周期独立判断: 全量(源数据远早于目标) / 增量(源数据更新) / 跳过.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    # 批量查询源/目标周期的最新/最早时间
    intervals = ("5m", "1d", "15m", "30m", "60m", "4h", "1wk", "1mo")
    try:
        last = {iv: repo.fetch_last_ts_map(market, iv, symbols) for iv in intervals}
        first = {iv: repo.fetch_first_ts_map(market, iv, symbols) for iv in intervals}
    except Exception:
        return

    total = 0
    full_init_count = 0
    skipped = 0
    for sym in symbols:
        w_15m = _decide_window(
            first["5m"].get(sym), last["5m"].get(sym),
            first["15m"].get(sym), last["15m"].get(sym),
            full_gap=timedelta(days=2), incr_window=_AGG_WINDOW_DAYS,
        )
        w_30m = _decide_window(
            first["5m"].get(sym), last["5m"].get(sym),
            first["30m"].get(sym), last["30m"].get(sym),
            full_gap=timedelta(days=2), incr_window=_AGG_WINDOW_DAYS,
        )
        w_60m = _decide_window(
            first["5m"].get(sym), last["5m"].get(sym),
            first["60m"].get(sym), last["60m"].get(sym),
            full_gap=timedelta(days=2), incr_window=_AGG_WINDOW_DAYS,
        )
        w_4h = _decide_window(
            first["5m"].get(sym), last["5m"].get(sym),
            first["4h"].get(sym), last["4h"].get(sym),
            full_gap=timedelta(days=2), incr_window=_AGG_WINDOW_DAYS,
        )
        w_1wk = _decide_window(
            first["1d"].get(sym), last["1d"].get(sym),
            first["1wk"].get(sym), last["1wk"].get(sym),
            full_gap=timedelta(days=30), incr_window=_RESAMPLE_WINDOW_DAYS,
        )
        w_1mo = _decide_window(
            first["1d"].get(sym), last["1d"].get(sym),
            first["1mo"].get(sym), last["1mo"].get(sym),
            full_gap=timedelta(days=60), incr_window=_RESAMPLE_WINDOW_DAYS,
        )

        windows = [w_15m, w_30m, w_60m, w_4h, w_1wk, w_1mo]
        if all(w is _NOOP for w in windows):
            skipped += 1
            continue

        is_full = any(w is None for w in windows)
        try:
            stats = await aggregate_derived_for_symbol(
                repo, market, sym,
                window_15m=w_15m, window_30m=w_30m,
                window_60m=w_60m, window_4h=w_4h,
                window_1wk=w_1wk, window_1mo=w_1mo,
            )
            new_bars = sum(stats.values())
            if new_bars > 0:
                total += new_bars
            if is_full:
                full_init_count += 1
                log.info("derived.full_init_done", symbol=sym, market=market,
                         new_bars=new_bars)
        except Exception as e:
            log.warning("derived.sweep_failed", symbol=sym, error=str(e))

    if skipped > 0 or total > 0:
        log.info("derived.sweep_done", market=market, total=len(symbols),
                 processed=len(symbols) - skipped, skipped=skipped,
                 full_init=full_init_count, new_bars=total)
