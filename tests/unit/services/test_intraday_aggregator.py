"""验证 aggregate_intraday 在三个市场上的 bar 时间戳序列与富途口径一致。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.domain.markets import Market
from core.domain.models import Bar
from core.services.intraday_aggregator import aggregate_intraday


def _gen_session_minutes(
    market: Market, local_date: date, freq_min: int, sessions: list[tuple[str, str]],
) -> list[Bar]:
    """合成某天 session 内每 freq_min 分钟一根 raw bar (ts = bar CLOSE), 升序。

    雷区 3: 所有 intraday(1m 除外) bar.ts = close 时刻 → UTC.
    第一根 bar 的 close = session_start + freq_min, 最后一根 close = session_end.
    """
    tz = ZoneInfo({
        "ashare": "Asia/Shanghai", "hk": "Asia/Hong_Kong",
        "us": "America/New_York", "crypto": "UTC",
    }[market])
    out: list[Bar] = []
    interval = f"{freq_min}m"
    for s_str, e_str in sessions:
        sh, sm = (int(x) for x in s_str.split(":"))
        if e_str == "24:00":
            e_local = datetime.combine(local_date + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        else:
            eh, em = (int(x) for x in e_str.split(":"))
            e_local = datetime(local_date.year, local_date.month, local_date.day, eh, em, tzinfo=tz)
        # 第一根 bar 的 close = session_start + freq_min
        cur = datetime(local_date.year, local_date.month, local_date.day, sh, sm, tzinfo=tz) + timedelta(minutes=freq_min)
        while cur <= e_local:
            ts_utc = cur.astimezone(timezone.utc)
            out.append(Bar(
                market=market, symbol="X", ts=ts_utc,
                open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                close=Decimal("100.5"), volume=1000, interval=interval,
            ))
            cur += timedelta(minutes=freq_min)
    return out


def _local_hhmm(ts: datetime, market: Market) -> str:
    tz_name = {
        "ashare": "Asia/Shanghai", "hk": "Asia/Hong_Kong",
        "us": "America/New_York", "crypto": "UTC",
    }[market]
    return ts.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")


def test_ashare_60m_4_bars_per_day():
    raw = _gen_session_minutes(
        "ashare", date(2026, 5, 18), 5,
        [("09:30", "11:30"), ("13:00", "15:00")],
    )
    out = aggregate_intraday(raw, "ashare", 60)
    assert [_local_hhmm(b.ts, "ashare") for b in out] == ["10:30", "11:30", "14:00", "15:00"]
    assert all(b.interval == "60m" for b in out)


def test_ashare_4h_2_bars_per_day():
    raw = _gen_session_minutes(
        "ashare", date(2026, 5, 18), 5,
        [("09:30", "11:30"), ("13:00", "15:00")],
    )
    out = aggregate_intraday(raw, "ashare", 240)
    assert [_local_hhmm(b.ts, "ashare") for b in out] == ["11:30", "15:00"]


def test_hk_60m_6_bars_per_day():
    raw = _gen_session_minutes(
        "hk", date(2026, 5, 18), 5,
        [("09:30", "12:00"), ("13:00", "16:00")],
    )
    out = aggregate_intraday(raw, "hk", 60)
    assert [_local_hhmm(b.ts, "hk") for b in out] == [
        "10:30", "11:30", "12:00", "14:00", "15:00", "16:00",
    ]


def test_hk_4h_2_bars_per_day():
    raw = _gen_session_minutes(
        "hk", date(2026, 5, 18), 5,
        [("09:30", "12:00"), ("13:00", "16:00")],
    )
    out = aggregate_intraday(raw, "hk", 240)
    assert [_local_hhmm(b.ts, "hk") for b in out] == ["12:00", "16:00"]


def test_us_60m_17_bars_per_day():
    raw = _gen_session_minutes(
        "us", date(2026, 5, 18), 1,  # 美股用 1m 模拟
        [("04:00", "09:30"), ("09:30", "16:00"), ("16:00", "20:00")],
    )
    out = aggregate_intraday(raw, "us", 60)
    expected = [
        "05:00", "06:00", "07:00", "08:00", "09:00", "09:30",
        "10:30", "11:30", "12:30", "13:30", "14:30", "15:30",
        "16:00", "17:00", "18:00", "19:00", "20:00",
    ]
    assert [_local_hhmm(b.ts, "us") for b in out] == expected


def test_us_4h_5_bars_per_day():
    raw = _gen_session_minutes(
        "us", date(2026, 5, 18), 1,
        [("04:00", "09:30"), ("09:30", "16:00"), ("16:00", "20:00")],
    )
    out = aggregate_intraday(raw, "us", 240)
    assert [_local_hhmm(b.ts, "us") for b in out] == [
        "08:00", "09:30", "13:30", "16:00", "20:00",
    ]


def test_ohlcv_aggregation_correct():
    """验证桶内 OHLCV 聚合: open=首根, high=max, low=min, close=末根, volume=sum

    raw bar.ts = CLOSE 语义(雷区 3). 12 根 5m bar close 时刻 9:35 / 9:40 / ... / 10:30
    全部落入 9:30-10:30 这一根 60m bucket(open<ts<=close,即 9:35..10:30 都 ≤ 10:30)。
    """
    tz = ZoneInfo("Asia/Shanghai")
    raw = []
    for i, (h, m) in enumerate([(9, 35), (9, 40), (9, 45), (9, 50), (9, 55), (10, 0),
                                  (10, 5), (10, 10), (10, 15), (10, 20), (10, 25), (10, 30)]):
        cur = datetime(2026, 5, 18, h, m, tzinfo=tz)
        raw.append(Bar(
            market="ashare", symbol="X", ts=cur.astimezone(timezone.utc),
            open=Decimal(f"{100 + i}"), high=Decimal(f"{105 + i}"),
            low=Decimal(f"{95 + i}"), close=Decimal(f"{102 + i}"),
            volume=1000, interval="5m",
        ))
    out = aggregate_intraday(raw, "ashare", 60)
    assert len(out) == 1
    bar = out[0]
    assert bar.open == Decimal("100")  # 首根 open
    assert bar.close == Decimal("113")  # 末根 close (102 + 11)
    assert bar.high == Decimal("116")  # max(105..116)
    assert bar.low == Decimal("95")    # min(95..106)
    assert bar.volume == 12000


def test_empty_raw_returns_empty():
    assert aggregate_intraday([], "ashare", 60) == []
