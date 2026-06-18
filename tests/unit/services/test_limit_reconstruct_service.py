from __future__ import annotations

import pandas as pd

from core.services.limit_reconstruct_service import board_limit_pct, reconstruct_from_bars


def test_board_limit_pct():
    assert board_limit_pct("600000.SH") == 0.10
    assert board_limit_pct("000001.SZ") == 0.10
    assert board_limit_pct("300750.SZ") == 0.20
    assert board_limit_pct("688981.SH") == 0.20
    assert board_limit_pct("830799.BJ") == 0.30
    assert board_limit_pct("920083.BJ") == 0.30


def _bar(symbol, date, o, h, low, c):
    return {"symbol": symbol, "ts": f"{date} 00:00:00+08:00",
            "open": o, "high": h, "low": low, "close": c, "amount": 1e8}


def test_reconstruct_limit_up_ladder_and_previous():
    # 600000: day1 基准 → day2 +10%封死(1板) → day3 +10%封死(2板) → day4 -9%(昨板今表现)
    rows = [
        _bar("600000.SH", "2024-01-02", 10.0, 10.0, 9.8, 10.0),
        _bar("600000.SH", "2024-01-03", 10.3, 11.0, 10.2, 11.0),   # +10% 封死 → limit_up 1板
        _bar("600000.SH", "2024-01-04", 11.5, 12.1, 11.4, 12.1),   # +10% 封死 → limit_up 2板; 昨板 previous
        _bar("600000.SH", "2024-01-05", 12.0, 12.3, 10.9, 11.0),   # -9.1% 未封 → 昨板 previous(亏)
    ]
    items = reconstruct_from_bars(pd.DataFrame(rows))
    lu = [it for it in items if it.pool_type == "limit_up"]
    prev = [it for it in items if it.pool_type == "previous"]

    assert [it.trade_date for it in lu] == ["2024-01-03", "2024-01-04"]
    assert [it.ladder_count for it in lu] == [1, 2]
    # previous: 01-04(昨日01-03涨停, 昨日连板=1) + 01-05(昨日01-04涨停, 昨日连板=2, 今日-9%)
    assert [it.trade_date for it in prev] == ["2024-01-04", "2024-01-05"]
    assert [it.ladder_count for it in prev] == [1, 2]
    assert prev[-1].change_pct < -8  # 昨板次日大跌


def test_reconstruct_broken_and_down():
    # 摸板未封=炸板; 跌停
    rows = [
        _bar("000001.SZ", "2024-02-01", 10.0, 10.0, 9.9, 10.0),
        _bar("000001.SZ", "2024-02-02", 10.5, 11.0, 10.3, 10.6),   # 摸11(+10%)但收10.6 → 炸板
        _bar("000001.SZ", "2024-02-05", 9.6, 9.7, 9.54, 9.54),     # -10% 封跌停
    ]
    items = reconstruct_from_bars(pd.DataFrame(rows))
    types = {it.trade_date: it.pool_type for it in items if it.pool_type in ("broken_limit", "down_limit")}
    assert types.get("2024-02-02") == "broken_limit"
    assert types.get("2024-02-05") == "down_limit"


def test_reconstruct_empty():
    assert reconstruct_from_bars(pd.DataFrame()) == []
