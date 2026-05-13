from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.adapters.ashare import AShareAdapter


@pytest.fixture
def mock_akshare_snapshot_df():
    return pd.DataFrame([
        {"代码": "000858", "名称": "五粮液", "最新价": 180.50, "涨跌幅": 1.25, "成交量": 12000},
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1580.0, "涨跌幅": -0.5, "成交量": 3000},
    ])


@pytest.mark.asyncio
async def test_fetch_snapshot_uses_primary_akshare(mock_akshare_snapshot_df):
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", return_value=mock_akshare_snapshot_df):
        adapter = AShareAdapter()
        quotes = await adapter.fetch_snapshot(["000858.SZ", "600519.SH"])
    assert len(quotes) == 2
    wuliangye = next(q for q in quotes if q.symbol == "000858.SZ")
    assert wuliangye.price == Decimal("180.50")
    assert wuliangye.change_pct == pytest.approx(1.25)
    assert wuliangye.source == "akshare"


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_mootdx(mock_akshare_snapshot_df):
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", side_effect=RuntimeError("boom")), \
         patch("core.adapters.ashare.AShareAdapter._fetch_snapshot_mootdx") as mock_mootdx:
        mock_mootdx.return_value = [
            MagicMock(symbol="000858.SZ", price=Decimal("180"), change_pct=0, volume=0, source="mootdx")
        ]
        adapter = AShareAdapter()
        quotes = await adapter.fetch_snapshot(["000858.SZ"])
    assert mock_mootdx.called
    assert quotes[0].source == "mootdx"


@pytest.mark.asyncio
async def test_circuit_opens_after_3_failures():
    adapter = AShareAdapter()
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", side_effect=RuntimeError("boom")), \
         patch.object(AShareAdapter, "_fetch_snapshot_mootdx", side_effect=RuntimeError("boom2")):
        for _ in range(3):
            with pytest.raises(Exception):
                await adapter.fetch_snapshot(["000858.SZ"])
    assert adapter.primary_cb.state == "open"


@pytest.mark.asyncio
async def test_health_reports_circuit_state():
    adapter = AShareAdapter()
    with patch("core.adapters.ashare.ak.stock_zh_index_spot_em", return_value=pd.DataFrame([
        {"代码": "000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": 0, "成交量": 1}
    ])):
        h = await adapter.health()
    assert h.state == "ok"
    assert h.name == "ashare"
