"""周/月线进行中根合成 (盘中实时态)。

根因(2026-06-08): A股/美股的进行中态组件 (quote_bar_ticker / bar_ticker) 用
固定分钟数切桶 (_INTERVAL_MIN 到 4h), 表达不了"周=5交易日/月=自然月"的桶, 故
1wk/1mo 盘中无进行中根 —— 当周/当月线只在收盘后 daily_settlement resample 生成,
开盘时不出现、盘中不随价跳。crypto 因 Binance WS 直推 1wk/1mo 才有。

方案A: 复用 daily_settlement 的 resample 口径 (W-FRI / ME), 但把"今日"那根用
实时价合成的临时日线补上, 再 resample 取最后一根 = 当周/当月进行中根 (final=false)。
口径与收盘后一致 (同 pandas resample), 不会盘中/收盘不符。纯函数, 便于测试。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.domain.models import Bar

# target_iv → pandas resample freq (与 aggregate_derived._resample_one 同源)
RESAMPLE_FREQ = {"1wk": "W-FRI", "1mo": "ME"}


def synthesize_provisional(
    daily: list[Bar], *, market: str, symbol: str, target_iv: str,
    today_ts: datetime, price: float, volume: int = 0,
) -> Bar | None:
    """用已收线日线 + 今日实时价合成当周/当月进行中根 (final 由调用方标 false)。

    daily: 已收线日线 (升序或乱序皆可, 内部排序), 含今日之前的本周/本月日线。
    today_ts: 今日交易日 UTC ts (与 1d bar ts 同口径: BJT 自然日 00:00 = UTC(D-1)16:00)。
    price: 当前实时价 (作今日日线的 close, 同时参与 high/low)。
    返回当周/当月进行中根 Bar; daily 为空且无今日价 → None。
    """
    import pandas as pd

    freq = RESAMPLE_FREQ.get(target_iv)
    if freq is None:
        return None

    rows = [
        {"ts": b.ts, "o": float(b.open), "h": float(b.high),
         "l": float(b.low), "c": float(b.close), "v": int(b.volume)}
        for b in daily
    ]
    # 今日临时日线: open=今日已有日线开盘价(若有)否则用 price; high/low/close 纳入实时价。
    # 若 daily 已含今日同 ts 的根(收盘后场景), 用实时价覆盖其 close/high/low。
    rows = [r for r in rows if r["ts"] != today_ts]
    rows.append({"ts": today_ts, "o": price, "h": price, "l": price,
                 "c": price, "v": int(volume)})

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    resampled = df.resample(freq).agg(
        {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}
    ).dropna()
    if resampled.empty:
        return None

    idx = resampled.index[-1]
    r = resampled.iloc[-1]
    return Bar(
        market=market, symbol=symbol,
        ts=idx.to_pydatetime().replace(tzinfo=timezone.utc),
        open=Decimal(str(r.o)), high=Decimal(str(r.h)),
        low=Decimal(str(r.l)), close=Decimal(str(r.c)),
        volume=int(r.v), interval=target_iv,
    )
