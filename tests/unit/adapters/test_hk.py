from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from core.adapters.hk import HKAdapter


@pytest.fixture
def mock_hk_spot_df():
    return pd.DataFrame([
        {"代码": "00700", "名称": "腾讯控股", "最新价": 380.0, "涨跌幅": 0.8, "成交量": 5000},
        {"代码": "09988", "名称": "阿里巴巴-W", "最新价": 78.5, "涨跌幅": -1.2, "成交量": 7000},
    ])


@pytest.mark.asyncio
async def test_fetch_snapshot_primary(mock_hk_spot_df):
    with patch("core.adapters.hk.ak.stock_hk_spot_em", return_value=mock_hk_spot_df):
        adapter = HKAdapter()
        quotes = await adapter.fetch_snapshot(["00700.HK", "09988.HK"])
    assert len(quotes) == 2
    assert all(q.market == "hk" for q in quotes)
    tencent = next(q for q in quotes if q.symbol == "00700.HK")
    assert tencent.price == Decimal("380.0")
    assert tencent.source == "akshare"


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_yfinance():
    with patch("core.adapters.hk.ak.stock_hk_spot_em", side_effect=RuntimeError("blocked")), \
         patch("core.adapters.hk.HKAdapter._fetch_snapshot_yfinance") as mock_yf:
        mock_yf.return_value = []
        adapter = HKAdapter()
        await adapter.fetch_snapshot(["00700.HK"])
    assert mock_yf.called
