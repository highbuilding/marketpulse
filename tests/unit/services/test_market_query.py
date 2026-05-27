from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from core.services.market_query import MarketQueryService


_A_SPOT_DF = pd.DataFrame([
    {"代码": "920469", "名称": "富恒新材", "最新价": 8.17, "涨跌幅": 29.889,
     "成交量": 15819179, "成交额": 119045385},
    {"代码": "300210", "名称": "森远股份", "最新价": 12.05, "涨跌幅": 20.02,
     "成交量": 9000000, "成交额": 108000000},
    {"代码": "688496", "名称": "*ST清越", "最新价": 2.78, "涨跌幅": -20.115,
     "成交量": 763124, "成交额": 2121485},
])


@pytest.mark.asyncio
async def test_top_ashare_gainers_uses_ak_call():
    svc = MarketQueryService()
    with patch("core.services.market_query.ak_call", AsyncMock(return_value=_A_SPOT_DF)) as mocked:
        rows = await svc.top_ashare(direction="desc", limit=2)

    mocked.assert_awaited_once()
    assert mocked.call_args.args[0] == "stock_zh_a_spot_em"
    assert rows[0].symbol == "920469.BJ"
    assert rows[0].change_pct == pytest.approx(29.889)
    assert rows[1].symbol == "300210.SZ"


@pytest.mark.asyncio
async def test_top_ashare_losers_uses_ak_call():
    svc = MarketQueryService()
    with patch("core.services.market_query.ak_call", AsyncMock(return_value=_A_SPOT_DF)):
        rows = await svc.top_ashare(direction="asc", limit=1)

    assert rows[0].symbol == "688496.SH"
    assert rows[0].change_pct < 0


@pytest.mark.asyncio
async def test_all_ashare_parses_amount_and_volume():
    svc = MarketQueryService()
    with patch("core.services.market_query.ak_call", AsyncMock(return_value=_A_SPOT_DF)):
        rows = await svc.all_ashare(limit=10)

    assert len(rows) == 3
    assert rows[0].amount == pytest.approx(119045385)
    assert rows[0].volume == 15819179


@pytest.mark.asyncio
async def test_sectors_ashare_combines_industry_and_concept():
    svc = MarketQueryService()
    industry_df = pd.DataFrame([
        {"板块名称": "船舶制造", "最新价": 10, "涨跌幅": 4.14, "上涨家数": 5,
         "下跌家数": 3, "领涨股票": "江苏国信", "领涨股票-涨跌幅": 4.14},
    ])
    concept_df = pd.DataFrame([
        {"板块名称": "机器人概念", "最新价": 10, "涨跌幅": 6.2, "上涨家数": 20,
         "下跌家数": 5, "领涨股票": "测试股", "领涨股票-涨跌幅": 12.3},
    ])
    with patch("core.services.market_query.ak_call", AsyncMock(side_effect=[industry_df, concept_df])) as mocked:
        rows = await svc.sectors_ashare()

    assert [call.args[0] for call in mocked.await_args_list] == [
        "stock_board_industry_name_em",
        "stock_board_concept_name_em",
    ]
    assert rows[0].code == "concept:机器人概念"
    assert rows[1].code == "industry:船舶制造"
    assert rows[0].company_count == 25


@pytest.mark.asyncio
async def test_sector_constituents_routes_by_kind():
    svc = MarketQueryService()
    cons_df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1250, "涨跌幅": 1.2,
         "成交量": 100, "成交额": 125000},
    ])
    with patch("core.services.market_query.ak_call", AsyncMock(return_value=cons_df)) as mocked:
        rows = await svc.sector_constituents_ashare("concept:白酒概念", limit=8)

    assert mocked.call_args.args[0] == "stock_board_concept_cons_em"
    assert mocked.call_args.kwargs["symbol"] == "白酒概念"
    assert rows[0].symbol == "600519.SH"


@pytest.mark.asyncio
async def test_top_hk_uses_ak_call():
    svc = MarketQueryService()
    hk_df = pd.DataFrame([
        {"代码": "01631", "名称": "REF HOLDINGS", "最新价": 1.32, "涨跌幅": 131.57,
         "成交量": 18940000, "成交额": 20883950},
    ])
    with patch("core.services.market_query.ak_call", AsyncMock(return_value=hk_df)) as mocked:
        rows = await svc.top_hk(direction="desc", limit=10)

    assert mocked.call_args.args[0] == "stock_hk_spot_em"
    assert rows[0].symbol == "01631.HK"
    assert rows[0].change_pct == pytest.approx(131.57)

