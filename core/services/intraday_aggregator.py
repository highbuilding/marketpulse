"""Intraday OHLCV 聚合器:把原始 1m / 5m bar 按 market_sessions 切桶 → 富途口径 60m / 4h。

入参 raw_bars 必须是同一 symbol 的连续 intraday bar, 按 ts 升序。

bar.ts 语义统一: 所有 intraday(1m 除外)bar.ts = bar **close** 时刻。
桶的开闭区间约定: (open_utc, close_utc]。即 raw bar ts == close_utc 算入当前桶,
ts == open_utc 算入上一桶(因为它是上一桶的 close)。这与雷区 3 一致。

注意: 美股 5m / A 股 5m / Binance 5m 的 raw bar.ts 在 adapter 层已经被 +5min
转换成 close 语义(见 ashare.py / us.py adapter 出口处理)。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.market_sessions import IntradayMinutes, bucket_grid
from core.domain.markets import Market
from core.domain.models import Bar


def aggregate_intraday(
    raw_bars: list[Bar], market: Market, interval_minutes: IntradayMinutes,
) -> list[Bar]:
    """把 raw_bars 聚合成 60m 或 4h(富途口径)。

    raw_bars: 任意更小粒度(美股 1m / 其他 5m)的 bar, ts 必须是 close 语义。空列表返回空。
    """
    if not raw_bars:
        return []
    target_interval = "4h" if interval_minutes == 240 else f"{interval_minutes}m"
    sample = raw_bars[0]

    # 按 raw bar 落在哪个 (local_date, bucket_idx) 分组
    # local_date 从 raw bar 的市场本地时区 day 取
    from core.domain.market_sessions import MARKET_TZ
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(MARKET_TZ[market])

    # 收集涉及到的所有本地日期 (raw bar 跨日时多个)
    dates: set = set()
    for b in raw_bars:
        dates.add(b.ts.astimezone(tz).date())
    # 美股盘后跨 UTC 日,本地仍同一交易日,无影响 — bucket_grid 用 local_date

    # 预生成所有日期的 bucket
    all_buckets: list[tuple[datetime, datetime]] = []
    for d in sorted(dates):
        all_buckets.extend(bucket_grid(market, d, interval_minutes))
    if not all_buckets:
        return []

    # 桶内累计 OHLCV
    bucket_data: dict[datetime, dict] = {}  # key = close_utc

    for b in raw_bars:
        # raw bar.ts 是 close 语义 → 落在 (open, close] 的桶里
        for open_utc, close_utc in all_buckets:
            if open_utc < b.ts <= close_utc:
                d = bucket_data.setdefault(close_utc, {
                    "open": b.open, "high": b.high, "low": b.low,
                    "close": b.close, "volume": 0, "first_ts": b.ts,
                })
                if b.ts < d["first_ts"]:
                    d["open"] = b.open
                    d["first_ts"] = b.ts
                if b.high > d["high"]:
                    d["high"] = b.high
                if b.low < d["low"]:
                    d["low"] = b.low
                d["close"] = b.close  # 假设 raw_bars 已按 ts 升序,最后一根赢
                d["volume"] += b.volume
                break

    out: list[Bar] = []
    for _, close_utc in all_buckets:
        d = bucket_data.get(close_utc)
        if d is None:
            continue
        out.append(Bar(
            market=sample.market, symbol=sample.symbol,
            ts=close_utc.astimezone(timezone.utc),
            open=d["open"], high=d["high"], low=d["low"], close=d["close"],
            volume=d["volume"], interval=target_interval,
        ))
    return out
