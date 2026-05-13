import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.services.market_query import MarketQueryService, _sina_to_symbol


_A_RANK_SAMPLE = json.dumps([
    {"symbol": "bj920469", "code": "920469", "name": "富恒新材",
     "trade": "8.170", "changepercent": 29.889, "volume": 15819179, "amount": 119045385},
    {"symbol": "sz300210", "code": "300210", "name": "森远股份",
     "trade": "12.050", "changepercent": 20.02, "volume": 9000000, "amount": 108000000},
])

_A_LOSERS_SAMPLE = json.dumps([
    {"symbol": "sh688496", "code": "688496", "name": "*ST清越",
     "trade": "2.780", "changepercent": -20.115, "volume": 763124, "amount": 2121485},
])

_HK_RANK_SAMPLE = json.dumps([
    {"symbol": "01631", "name": "REF HOLDINGS", "lasttrade": "1.32000",
     "changepercent": "131.57896", "volume": "18940000", "amount": "20883950"},
])

_SECTOR_DF = pd.DataFrame([
    {"label": "new_dzhx", "板块": "电子化学品", "公司家数": 20, "平均价格": 45.26,
     "个股-涨跌幅": 20.0, "个股-当前价": 117.6, "个股-涨跌额": 19.6, "股票名称": "中船特气",
     "涨跌幅": 4.07, "总成交量": 1213.28, "总成交额": 549.10, "上涨家数": 17, "下跌家数": 3},
    {"label": "new_cbzz", "板块": "船舶制造", "公司家数": 8, "平均价格": 17.22,
     "个股-涨跌幅": 4.14, "个股-当前价": 8.55, "个股-涨跌额": 0.34, "股票名称": "江苏国信",
     "涨跌幅": 4.14, "总成交量": 123, "总成交额": 456, "上涨家数": 5, "下跌家数": 3},
])


def test_sina_to_symbol():
    assert _sina_to_symbol("sh600519") == "600519.SH"
    assert _sina_to_symbol("sz000858") == "000858.SZ"
    assert _sina_to_symbol("bj920469") == "920469.BJ"
    assert _sina_to_symbol("xx") == "xx"


@pytest.mark.asyncio
async def test_top_ashare_gainers():
    svc = MarketQueryService()
    fake = MagicMock()
    fake.text = _A_RANK_SAMPLE
    fake.raise_for_status = MagicMock()
    with patch.object(svc._session, "get", return_value=fake) as m:
        rows = await svc.top_ashare(direction="desc", limit=10)
    assert len(rows) == 2
    assert rows[0].symbol == "920469.BJ"
    assert rows[0].change_pct == pytest.approx(29.889)
    assert rows[0].name == "富恒新材"
    # 验证参数:asc=0 表示 desc
    params = m.call_args.kwargs["params"]
    assert params["asc"] == 0
    assert params["node"] == "hs_a"


@pytest.mark.asyncio
async def test_top_ashare_losers_uses_asc_true():
    svc = MarketQueryService()
    fake = MagicMock()
    fake.text = _A_LOSERS_SAMPLE
    fake.raise_for_status = MagicMock()
    with patch.object(svc._session, "get", return_value=fake) as m:
        rows = await svc.top_ashare(direction="asc", limit=5)
    assert m.call_args.kwargs["params"]["asc"] == 1
    assert rows[0].change_pct < 0


@pytest.mark.asyncio
async def test_top_hk():
    svc = MarketQueryService()
    fake = MagicMock()
    fake.text = _HK_RANK_SAMPLE
    fake.raise_for_status = MagicMock()
    with patch.object(svc._session, "get", return_value=fake):
        rows = await svc.top_hk(direction="desc", limit=10)
    assert rows[0].symbol == "01631.HK"
    assert rows[0].change_pct == pytest.approx(131.57896)
    assert rows[0].price == pytest.approx(1.32)


@pytest.mark.asyncio
async def test_sectors_ashare():
    svc = MarketQueryService()
    with patch("core.services.market_query.ak.stock_sector_spot", return_value=_SECTOR_DF):
        rows = await svc.sectors_ashare()
    assert len(rows) == 2
    assert rows[0].name == "电子化学品"
    assert rows[0].change_pct == pytest.approx(4.07)
    assert rows[0].leader_name == "中船特气"
    assert rows[0].leader_change_pct == pytest.approx(20.0)
