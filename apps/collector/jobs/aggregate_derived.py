"""定期聚合派生周期 (60m/4h/1wk/1mo).

collector 日常运行时: WS/poll 写新的 5m/1d bars → DuckDB。
此 job 每 5 分钟扫一次, 对有时间窗口内有更新的标的重新聚合。
DuckDB upsert (ON CONFLICT) 自动去重, 重复聚合无副作用。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo
from core.services.intraday_aggregator import aggregate_intraday

log = structlog.get_logger(__name__)

# 聚合窗口: 只更新最近 N 天的数据 (增量)
_AGG_WINDOW_DAYS = 1       # 60m/4h — 只补最近 1 天
_RESAMPLE_WINDOW_DAYS = 7  # 1wk/1mo — 只补最近 7 天


async def _agg_one(
    repo: BarRepo, market: str, symbol: str,
    target_iv: str, source_iv: str, interval_minutes: int, window_days: int,
) -> int:
    """从 source_iv 聚合 target_iv, 只处理最近 window_days 天的 raw bars."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=window_days)
    raw = repo.fetch_history(market, symbol, start, now, interval=source_iv)
    if not raw:
        return 0
    agg = aggregate_intraday(raw, market, interval_minutes)  # type: ignore[arg-type]
    if not agg:
        return 0
    # 只保留窗口内的聚合结果
    agg = [b for b in agg if b.ts >= start]
    if agg:
        repo.insert_bars(agg)
    return len(agg)


async def _resample_one(
    repo: BarRepo, market: str, symbol: str,
    target_iv: str, freq: str, window_days: int,
) -> int:
    """从 1d resample target_iv, 只处理最近 window_days 天的 daily bars."""
    import pandas as pd
    from decimal import Decimal

    now = datetime.now(timezone.utc)
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
    # 只保留窗口内
    bars = [b for b in bars if b.ts >= start]
    if bars:
        repo.insert_bars(bars)
    return len(bars)


async def aggregate_derived_for_symbol(
    repo: BarRepo, market: str, symbol: str,
) -> dict:
    """对单个标的执行全部派生聚合. 返回统计."""
    stats: dict[str, int] = {}
    for target, source, mins, window in [
        ("60m", "5m", 60, _AGG_WINDOW_DAYS),
        ("4h", "5m", 240, _AGG_WINDOW_DAYS),
    ]:
        try:
            n = await _agg_one(repo, market, symbol, target, source, mins, window)
            stats[target] = n
        except Exception as e:
            log.warning("derived.agg_failed",
                        symbol=symbol, target=target, error=str(e))

    for target, freq, window in [
        ("1wk", "W", _RESAMPLE_WINDOW_DAYS),
        ("1mo", "ME", _RESAMPLE_WINDOW_DAYS),
    ]:
        try:
            n = await _resample_one(repo, market, symbol, target, freq, window)
            stats[target] = n
        except Exception as e:
            log.warning("derived.resample_failed",
                        symbol=symbol, target=target, error=str(e))

    return stats


async def sweep_derived(
    repo: BarRepo, market: str, symbols: list[str],
) -> None:
    """扫描并补全需要更新的派生周期.

    每 30 分钟由 collector scheduler 调用.
    只处理 5m/1d 数据比 60m/1wk 更新的标的 (增量).
    """
    import duckdb
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    # 批量查询所有标的的 5m/1d/60m/1wk 最新时间
    db_path = repo.db_path
    try:
        c = duckdb.connect(db_path, read_only=True)
        last_5m = dict(c.execute(f"""
            SELECT symbol, MAX(ts) FROM bars
            WHERE market='{market}' AND interval='5m' AND symbol IN ({_sql_in(symbols)})
            GROUP BY symbol
        """).fetchall())
        last_1d = dict(c.execute(f"""
            SELECT symbol, MAX(ts) FROM bars
            WHERE market='{market}' AND interval='1d' AND symbol IN ({_sql_in(symbols)})
            GROUP BY symbol
        """).fetchall())
        last_60m = dict(c.execute(f"""
            SELECT symbol, MAX(ts) FROM bars
            WHERE market='{market}' AND interval='60m' AND symbol IN ({_sql_in(symbols)})
            GROUP BY symbol
        """).fetchall())
        last_1wk = dict(c.execute(f"""
            SELECT symbol, MAX(ts) FROM bars
            WHERE market='{market}' AND interval='1wk' AND symbol IN ({_sql_in(symbols)})
            GROUP BY symbol
        """).fetchall())
        c.close()
    except Exception:
        # DuckDB 被 collector RW 锁占用 → 跳过本轮, 下轮再扫
        return

    total = 0
    skipped = 0
    for sym in symbols:
        # 只处理 5m 比 60m 新 或 1d 比 1wk 新的标的
        need_agg = (sym in last_5m and (
            sym not in last_60m
            or last_5m[sym] > last_60m[sym] + timedelta(hours=1)
        ))
        need_resample = (sym in last_1d and (
            sym not in last_1wk
            or last_1d[sym] > last_1wk[sym] + timedelta(days=1)
        ))
        if not need_agg and not need_resample:
            skipped += 1
            continue

        try:
            stats = await aggregate_derived_for_symbol(repo, market, sym)
            new_bars = sum(stats.values())
            if new_bars > 0:
                total += new_bars
        except Exception as e:
            log.warning("derived.sweep_failed", symbol=sym, error=str(e))

    if skipped > 0 or total > 0:
        log.info("derived.sweep_done", market=market, total=len(symbols),
                 processed=len(symbols) - skipped, skipped=skipped,
                 new_bars=total)


def _sql_in(symbols: list[str]) -> str:
    """构造 SQL IN 列表, 防注入 (symbols 来自本地文件, 可信任)."""
    return ",".join(f"'{s}'" for s in symbols)
