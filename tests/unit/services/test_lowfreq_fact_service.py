from __future__ import annotations

import pandas as pd
import pytest

from core.services.lowfreq_fact_service import (
    parse_lhb_df,
    parse_notice_df,
    parse_ths_fund_flow_df,
)


def test_parse_lhb_df():
    df = pd.DataFrame([
        {
            "代码": "600519",
            "名称": "贵州茅台",
            "上榜原因": "日涨幅偏离值达7%",
            "龙虎榜净买额": 120000000,
            "龙虎榜买入额": 300000000,
            "龙虎榜卖出额": 180000000,
            "换手率": 2.3,
        },
    ])

    rows = parse_lhb_df(df, trade_date="2026-06-17")

    assert rows[0]["symbol"] == "600519.SH"
    assert rows[0]["net_buy"] == pytest.approx(120000000)
    assert rows[0]["reason"] == "日涨幅偏离值达7%"


def test_parse_notice_df():
    df = pd.DataFrame([
        {"代码": "000001", "名称": "平安银行", "公告标题": "年度报告", "公告类型": "定期报告"},
    ])

    rows = parse_notice_df(df, trade_date="2026-06-17")

    assert rows[0]["symbol"] == "000001.SZ"
    assert rows[0]["title"] == "年度报告"
    assert rows[0]["notice_type"] == "定期报告"


def test_parse_ths_fund_flow_df():
    df = pd.DataFrame([
        {"行业": "半导体", "涨跌幅": 2.5, "净额": 820000000},
    ])

    rows = parse_ths_fund_flow_df(
        df, trade_date="2026-06-17", flow_type="industry")

    assert rows[0]["subject"] == "半导体"
    assert rows[0]["change_pct"] == pytest.approx(2.5)
    assert rows[0]["net_inflow"] == pytest.approx(820000000)
