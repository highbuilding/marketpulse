from __future__ import annotations

import pandas as pd
import pytest

from core.services.market_changes_service import (
    _decode_related,
    parse_board_change_df,
    parse_changes_df,
)


@pytest.mark.parametrize(
    "info,price,pct",
    [
        ("0.036636,12.45000,0.036636", 12.45, 3.6636),       # 火箭发射
        ("35.890000,184647,35.89000,0.099908", 35.89, 9.9908),  # 封涨停板 (含成交量整数)
        ("4.630000,0.099762", 4.63, 9.9762),                  # 打开涨停板
        ("0.019766,11.35000,0.019766", 11.35, 1.9766),        # 竞价上涨
        ("-0.027624,35.20000,-0.027624", 35.20, -2.7624),     # 竞价下跌 (负)
    ],
)
def test_decode_related(info, price, pct):
    p, c = _decode_related(info)
    assert p == pytest.approx(price)
    assert c == pytest.approx(pct)


def test_decode_related_empty():
    assert _decode_related(None) == (None, None)
    assert _decode_related("") == (None, None)


def test_parse_changes_df():
    df = pd.DataFrame([
        {"时间": "14:55:54", "代码": "002858", "名称": "力盛体育",
         "板块": "火箭发射", "相关信息": "0.036636,12.45000,0.036636"},
        {"时间": "09:24:16", "代码": "688616", "名称": "西力科技",
         "板块": "竞价上涨", "相关信息": "0.019766,11.35000,0.019766"},
    ])
    items = parse_changes_df(df, trade_date="2026-06-17")
    assert len(items) == 2
    a = items[0]
    assert a.symbol == "002858.SZ"
    assert a.change_type == "火箭发射"
    assert a.change_time == "14:55:54"
    assert a.price == pytest.approx(12.45)
    assert a.change_pct == pytest.approx(3.6636)
    assert items[1].symbol == "688616.SH"


def test_parse_changes_df_empty():
    assert parse_changes_df(pd.DataFrame(), trade_date="2026-06-17") == []


def test_parse_board_change_df():
    df = pd.DataFrame([
        {
            "板块名称": "融资融券", "涨跌幅": -0.09, "主力净流入": -1701969.92,
            "板块异动总次数": 10768,
            "板块异动最频繁个股及所属类型-股票代码": "920083",
            "板块异动最频繁个股及所属类型-股票名称": "金戈新材",
            "板块异动最频繁个股及所属类型-买卖方向": "大笔买入",
        },
    ])
    items = parse_board_change_df(df, trade_date="2026-06-17")
    assert len(items) == 1
    b = items[0]
    assert b.board_name == "融资融券"
    assert b.change_pct == pytest.approx(-0.09)
    assert b.main_net_inflow == pytest.approx(-1701969.92)
    assert b.change_total == 10768
    assert b.top_name == "金戈新材"
    assert b.top_direction == "大笔买入"
