from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.adapters.hk import HKAdapter, _to_sina_code


_SINA_HK = (
    'var hq_str_hk00700="TENCENT,腾讯控股,456.000,457.200,465.800,454.000,'
    '462.800,5.600,1.225,462.60001,462.79999,11090343806,24078954,0.000,0.000,'
    '683.000,454.000,2026/05/13,15:57";\n'
    'var hq_str_hkHSI="HSI,恒生指数,26369.990,26347.910,26458.900,26220.120,'
    '26376.990,29.080,0.110,0.00000,0.00000,267583167,14489879943,0.000,0.000,'
    '28056.100,22668.350,2026/05/13,16:02";\n'
)


def test_to_sina_code():
    assert _to_sina_code("00700.HK") == "hk00700"
    assert _to_sina_code("HSI.HK") == "hkHSI"


@pytest.mark.asyncio
async def test_fetch_snapshot_parses_sina():
    adapter = HKAdapter()
    fake = MagicMock()
    fake.text = _SINA_HK
    fake.encoding = "gbk"
    fake.raise_for_status = MagicMock()
    with patch.object(adapter._session, "get", return_value=fake):
        quotes = await adapter.fetch_snapshot(["00700.HK", "HSI.HK"])
    symbols = {q.symbol for q in quotes}
    assert "00700.HK" in symbols and "HSI.HK" in symbols
    tencent = next(q for q in quotes if q.symbol == "00700.HK")
    assert tencent.price == Decimal("462.8000")
    assert tencent.change_pct == pytest.approx(1.225, abs=0.01)
    assert tencent.source == "sina"


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_yfinance():
    adapter = HKAdapter()
    with patch.object(adapter._session, "get", side_effect=RuntimeError("blocked")), \
         patch.object(adapter, "_fetch_snapshot_yfinance") as mock_yf:
        mock_yf.return_value = []
        await adapter.fetch_snapshot(["00700.HK"])
    assert mock_yf.called
