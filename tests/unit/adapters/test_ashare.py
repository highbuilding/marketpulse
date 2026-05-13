from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.adapters.ashare import AShareAdapter, _to_sina_code


_SINA_RESPONSE = (
    'var hq_str_sh600519="贵州茅台,1354.500,1354.550,1344.090,1358.600,1338.000,'
    '1344.050,1344.090,5696787,7653257144.000,100,1344.050,300,1344.030,900,'
    '1344.020,400,1344.010,1700,1344.000,100,1344.090,200,1344.100,200,1344.120,'
    '2100,1344.130,200,1344.240,2026-05-13,15:00:03,00,";\n'
    'var hq_str_sz000858="五 粮 液,90.570,90.560,89.150,90.830,88.740,89.140,'
    '89.150,46720129,4173652012.860,500,89.140,100,89.130,4100,89.120,3200,'
    '89.110,19500,89.100,14628,89.150,6200,89.160,9300,89.170,5000,89.180,'
    '14500,89.190,2026-05-13,15:00:00,00";\n'
)


def test_to_sina_code():
    assert _to_sina_code("600519.SH") == "sh600519"
    assert _to_sina_code("000858.SZ") == "sz000858"
    # 也能从无后缀推断
    assert _to_sina_code("600519") == "sh600519"


@pytest.mark.asyncio
async def test_fetch_snapshot_parses_sina_response():
    adapter = AShareAdapter()
    fake = MagicMock()
    fake.text = _SINA_RESPONSE
    fake.encoding = "gbk"
    fake.raise_for_status = MagicMock()
    with patch.object(adapter._session, "get", return_value=fake):
        quotes = await adapter.fetch_snapshot(["600519.SH", "000858.SZ"])
    assert {q.symbol for q in quotes} == {"600519.SH", "000858.SZ"}
    moutai = next(q for q in quotes if q.symbol == "600519.SH")
    assert moutai.price == Decimal("1344.0900")
    assert moutai.source == "sina"
    # change_pct = (1344.09 - 1354.55) / 1354.55 * 100 ≈ -0.7723%
    assert moutai.change_pct == pytest.approx(-0.7723, abs=0.01)


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_mootdx():
    adapter = AShareAdapter()
    with patch.object(adapter._session, "get", side_effect=RuntimeError("blocked")), \
         patch("core.adapters.ashare.AShareAdapter._fetch_snapshot_mootdx") as mock_mootdx:
        mock_mootdx.return_value = []
        await adapter.fetch_snapshot(["000858.SZ"])
    assert mock_mootdx.called


@pytest.mark.asyncio
async def test_circuit_opens_after_3_failures():
    adapter = AShareAdapter()
    with patch.object(adapter._session, "get", side_effect=RuntimeError("boom")), \
         patch("core.adapters.ashare.AShareAdapter._fetch_snapshot_mootdx",
               side_effect=RuntimeError("boom2")):
        for _ in range(3):
            with pytest.raises(Exception):
                await adapter.fetch_snapshot(["000858.SZ"])
    assert adapter.primary_cb.state == "open"


@pytest.mark.asyncio
async def test_health_reports_ok_when_sina_responds():
    adapter = AShareAdapter()
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    with patch.object(adapter._session, "get", return_value=fake):
        h = await adapter.health()
    assert h.state == "ok"
    assert h.name == "ashare"
