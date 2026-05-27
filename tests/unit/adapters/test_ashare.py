from datetime import datetime, timezone, date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.adapters.ashare import AShareAdapter, _classify, _to_sina_code


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


_DAILY_DF = pd.DataFrame([
    {"date": date(2026, 5, 12), "open": 1340.0, "high": 1360.0, "low": 1330.0,
     "close": 1354.55, "volume": 5_000_000, "amount": 6_700_000_000, "turnover": 0.41},
    {"date": date(2026, 5, 13), "open": 1354.5, "high": 1358.6, "low": 1338.0,
     "close": 1344.09, "volume": 5_696_787, "amount": 7_653_257_144, "turnover": 0.47},
])

_5MIN_DF = pd.DataFrame([
    {"day": "2026-05-13 09:35:00", "open": 1350.0, "high": 1351.0, "low": 1349.0,
     "close": 1350.5, "volume": 100_000},
])


@pytest.mark.asyncio
async def test_fetch_history_uses_sina_daily():
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.stock_zh_a_daily", return_value=_DAILY_DF):
        bars = await adapter.fetch_history(
            "600519.SH",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    assert bars[1].close == Decimal("1344.09")
    assert bars[1].interval == "1d"


@pytest.mark.asyncio
async def test_fetch_intraday_5min():
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.stock_zh_a_minute", return_value=_5MIN_DF):
        bars = await adapter.fetch_intraday("600519.SH", freq="5")
    assert len(bars) == 1
    assert bars[0].interval == "5m"
    assert bars[0].close == Decimal("1350.5")


# ============== _classify 多标的回归 ==============


@pytest.mark.parametrize("symbol,expected", [
    # 个股
    ("600519.SH", "stock"),  # 贵州茅台
    ("600004.SH", "stock"),  # 白云机场 ← 回归 bug
    ("000858.SZ", "stock"),  # 五粮液
    ("300750.SZ", "stock"),  # 宁德时代
    ("002594.SZ", "stock"),  # 比亚迪
    ("688981.SH", "stock"),  # 中芯国际
    ("920469.BJ", "stock"),  # 富恒新材(北交所)
    # ETF (SH)
    ("510300.SH", "etf"),   # 沪深300ETF
    ("510500.SH", "etf"),   # 中证500ETF
    ("588000.SH", "etf"),   # 科创50ETF
    ("513050.SH", "etf"),   # 中概互联网ETF
    # ETF (SZ)
    ("159915.SZ", "etf"),   # 创业板ETF
    ("159949.SZ", "etf"),   # 创业板50ETF
    ("159995.SZ", "etf"),   # 半导体ETF
    # 指数
    ("000001.SH", "index"),  # 上证指数
    ("000300.SH", "index"),  # 沪深300
    ("000688.SH", "index"),  # 科创50指数
    ("000905.SH", "index"),  # 中证500指数
    ("399001.SZ", "index"),  # 深证成指
    ("399006.SZ", "index"),  # 创业板指
])
def test_classify_various_symbols(symbol, expected):
    assert _classify(symbol) == expected, f"{symbol} should be {expected}"


# ============== fetch_history 多源分发(stock / etf / index)==============

_STOCK_DAILY_DF = pd.DataFrame([
    {"date": date(2020, 1, 2), "open": 10.0, "high": 10.5, "low": 9.8,
     "close": 10.3, "volume": 1_000_000, "amount": 10_000_000, "turnover": 1.1},
    {"date": date(2026, 5, 13), "open": 18.0, "high": 18.5, "low": 17.5,
     "close": 18.2, "volume": 2_000_000, "amount": 36_000_000, "turnover": 2.2},
])

_ETF_DAILY_DF = pd.DataFrame([
    {"date": date(2020, 1, 2), "open": 3.0, "high": 3.1, "low": 2.95,
     "close": 3.05, "volume": 1_000_000_000},
    {"date": date(2026, 5, 13), "open": 4.9, "high": 5.0, "low": 4.85,
     "close": 4.95, "volume": 2_000_000_000},
])

_INDEX_DAILY_DF = pd.DataFrame([
    {"date": date(2020, 1, 2), "open": 3050, "high": 3080, "low": 3040,
     "close": 3070, "volume": 100_000_000},
    {"date": date(2026, 5, 13), "open": 4190, "high": 4245, "low": 4190,
     "close": 4242.57, "volume": 700_000_000},
])


@pytest.mark.asyncio
async def test_fetch_history_stock_routes_to_stock_zh_a_daily():
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.stock_zh_a_daily", return_value=_STOCK_DAILY_DF) as m, \
         patch("core.integrations.akshare.ak.fund_etf_hist_sina", side_effect=AssertionError("should not be called")), \
         patch("core.integrations.akshare.ak.stock_zh_index_daily", side_effect=AssertionError("should not be called")):
        bars = await adapter.fetch_history(
            "600004.SH",  # 白云机场
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    m.assert_called_once()
    assert len(bars) == 2
    assert bars[0].close == Decimal("10.3")
    assert bars[0].amount == 10_000_000
    assert bars[0].turnover == 1.1


@pytest.mark.asyncio
async def test_fetch_history_stock_supplements_amount_and_turnover_from_hist():
    daily_df = pd.DataFrame([
        {"date": date(2026, 5, 13), "open": 18.0, "high": 18.5, "low": 17.5,
         "close": 18.2, "volume": 2_000_000},
    ])
    hist_df = pd.DataFrame([
        {"日期": "2026-05-13", "成交额": 36_000_000, "换手率": 2.2},
    ])
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.stock_zh_a_daily", return_value=daily_df), \
         patch("core.integrations.akshare.ak.stock_zh_a_hist", return_value=hist_df) as hist:
        bars = await adapter.fetch_history(
            "600004.SH",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
    hist.assert_called_once()
    assert bars[0].amount == 36_000_000
    assert bars[0].turnover == 2.2


@pytest.mark.asyncio
async def test_fetch_history_etf_routes_to_fund_etf_hist_sina():
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.fund_etf_hist_sina", return_value=_ETF_DAILY_DF) as m, \
         patch("core.integrations.akshare.ak.stock_zh_a_daily", side_effect=AssertionError("should not be called")), \
         patch("core.integrations.akshare.ak.stock_zh_index_daily", side_effect=AssertionError("should not be called")):
        bars = await adapter.fetch_history(
            "510300.SH",  # 沪深300ETF
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    m.assert_called_once_with(symbol="sh510300")
    assert len(bars) == 2
    assert bars[0].close == Decimal("3.05")


@pytest.mark.asyncio
async def test_fetch_history_index_routes_to_stock_zh_index_daily():
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.stock_zh_index_daily", return_value=_INDEX_DAILY_DF) as m, \
         patch("core.integrations.akshare.ak.stock_zh_a_daily", side_effect=AssertionError("should not be called")), \
         patch("core.integrations.akshare.ak.fund_etf_hist_sina", side_effect=AssertionError("should not be called")):
        bars = await adapter.fetch_history(
            "000001.SH",  # 上证指数
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    m.assert_called_once_with(symbol="sh000001")
    assert len(bars) == 2
    assert bars[0].close == Decimal("3070")


@pytest.mark.asyncio
async def test_fetch_history_etf_filters_by_date_range():
    """ETF 接口返回全部历史(从 2012 起),需要按 [start, end] 后置过滤。"""
    big_df = pd.DataFrame([
        {"date": date(2015, 1, 1), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"date": date(2020, 6, 1), "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2},
        {"date": date(2026, 5, 13), "open": 5, "high": 5, "low": 5, "close": 5, "volume": 5},
    ])
    adapter = AShareAdapter()
    with patch("core.integrations.akshare.ak.fund_etf_hist_sina", return_value=big_df):
        bars = await adapter.fetch_history(
            "510300.SH",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
    # 应该只剩 2020-06-01 和 2026-05-13,2015-01-01 被过滤掉
    assert len(bars) == 2
    assert bars[0].close == Decimal("2")
    assert bars[1].close == Decimal("5")
