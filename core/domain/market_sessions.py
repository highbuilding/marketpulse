"""SSoT: 各市场交易 session + intraday bucket 网格。

设计:bar ts = bar close 时刻(UTC),session 末尾不足整 interval 自动成半棒,
session 之间硬断不混合 — 与富途客户端 K 线展示完全对齐。

bucket_grid(market, local_date, interval_minutes) 返回当日所有 bucket:
  [(open_utc, close_utc), ...]
其中 close_utc 即该 bucket 对应 bar 的 ts(雷区 3 延伸至 intraday)。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from core.domain.markets import Market

IntradayMinutes = Literal[60, 240]

# 各市场本地 wall-clock session 列表 (start_hhmm, end_hhmm)
SESSIONS: dict[Market, list[tuple[str, str]]] = {
    "ashare": [("09:30", "11:30"), ("13:00", "15:00")],
    "hk":     [("09:30", "12:00"), ("13:00", "16:00")],
    "us":     [("04:00", "09:30"), ("09:30", "16:00"), ("16:00", "20:00")],
    "crypto": [("00:00", "24:00")],
}

MARKET_TZ: dict[Market, str] = {
    "ashare": "Asia/Shanghai",
    "hk":     "Asia/Hong_Kong",
    "us":     "America/New_York",
    "crypto": "UTC",
}


def _hhmm_to_time(hhmm: str) -> time:
    if hhmm == "24:00":
        return time(23, 59, 59, 999_999)
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def bucket_grid(
    market: Market, local_date: date, interval_minutes: IntradayMinutes,
) -> list[tuple[datetime, datetime]]:
    """返回 local_date 当天 market 所有 intraday bucket 的 (open_utc, close_utc)。

    interval_minutes: 60 或 240 (4h)。
    每个 session 内从 session_start 起按 interval_minutes 切;末尾不足成半棒。
    """
    tz = ZoneInfo(MARKET_TZ[market])
    out: list[tuple[datetime, datetime]] = []
    delta = timedelta(minutes=interval_minutes)
    for s_str, e_str in SESSIONS[market]:
        s_local = datetime.combine(local_date, _hhmm_to_time(s_str), tzinfo=tz)
        e_local = datetime.combine(local_date, _hhmm_to_time(e_str), tzinfo=tz)
        # crypto 24:00 = 次日 00:00
        if e_str == "24:00":
            e_local = datetime.combine(
                local_date + timedelta(days=1), time(0, 0), tzinfo=tz,
            )
        cursor = s_local
        while cursor < e_local:
            nxt = min(cursor + delta, e_local)
            out.append((cursor.astimezone(timezone.utc), nxt.astimezone(timezone.utc)))
            cursor = nxt
    return out


def expected_bar_ts(
    market: Market, local_date: date, interval_minutes: IntradayMinutes,
) -> list[datetime]:
    """便捷封装:只要 close_utc 序列(等价于 bar.ts)。"""
    return [close for _, close in bucket_grid(market, local_date, interval_minutes)]


def is_market_session_open(market: Market, when: datetime | None = None) -> bool:
    """当前是否在该市场交易 session 内 (本市场时区)。

    crypto 永远 True; 其他市场要求落在 SESSIONS 任一区间内。
    周末 / 节假日由调用方自行 is_trading_day 门控, 本函数只判时段。
    """
    tz = ZoneInfo(MARKET_TZ[market])
    now_local = (when or datetime.now(timezone.utc)).astimezone(tz)
    cur = now_local.time()
    for s_str, e_str in SESSIONS[market]:
        s = _hhmm_to_time(s_str)
        e = _hhmm_to_time(e_str) if e_str != "24:00" else time(23, 59, 59, 999_999)
        if s <= cur <= e:
            return True
    return False
