from datetime import datetime, timezone, date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.adapters.ashare import AShareAdapter, _classify, _to_sina_code


def _ak_dispatch(mapping: dict) -> AsyncMock:
    """构造一个 AsyncMock 替代 ak_call: 按首参 func_name 分发返回值。

    未在 mapping 中的 func_name 会触发 AssertionError, 等价于旧测试里
    `side_effect=AssertionError("should not be called")` 的禁用语义。
    """
    async def _fake(func_name, *args, **kwargs):
        if func_name not in mapping:
            raise AssertionError(f"unexpected ak_call: {func_name}")
        return mapping[func_name]
    return AsyncMock(side_effect=_fake)


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
    fake = _ak_dispatch({
        "stock_zh_a_daily": _DAILY_DF,
        # supplement 调用允许返回空,不影响主路径
        "stock_zh_a_hist": pd.DataFrame(),
    })
    with patch("core.adapters.ashare.ak_call", fake):
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
    fake = _ak_dispatch({"stock_zh_a_minute": _5MIN_DF})
    with patch("core.adapters.ashare.ak_call", fake):
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
    fake = _ak_dispatch({
        "stock_zh_a_daily": _STOCK_DAILY_DF,
        "stock_zh_a_hist": pd.DataFrame(),  # supplement 路径,不影响主断言
    })
    with patch("core.adapters.ashare.ak_call", fake):
        bars = await adapter.fetch_history(
            "600004.SH",  # 白云机场
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    # 主路径必须命中 stock_zh_a_daily
    called_funcs = [c.args[0] for c in fake.call_args_list]
    assert "stock_zh_a_daily" in called_funcs
    assert "fund_etf_hist_sina" not in called_funcs
    assert "stock_zh_index_daily" not in called_funcs
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
    fake = _ak_dispatch({
        "stock_zh_a_daily": daily_df,
        "stock_zh_a_hist": hist_df,
    })
    # patch 掉收盘判定: 东财兜底是正交新行为(收盘后 sina 未覆盖今日才触发),
    # 本测试只验 sina 主路径 + 指标补充, 故强制非收盘态避免时变条件干扰。
    with patch("core.adapters.ashare.ak_call", fake), \
            patch("core.adapters.ashare.is_after_market_close", return_value=False):
        bars = await adapter.fetch_history(
            "600004.SH",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
    called_funcs = [c.args[0] for c in fake.call_args_list]
    assert called_funcs.count("stock_zh_a_hist") == 1
    assert bars[0].amount == 36_000_000
    assert bars[0].turnover == 2.2


@pytest.mark.asyncio
async def test_fetch_history_etf_routes_to_fund_etf_hist_sina():
    adapter = AShareAdapter()
    fake = _ak_dispatch({"fund_etf_hist_sina": _ETF_DAILY_DF})
    with patch("core.adapters.ashare.ak_call", fake):
        bars = await adapter.fetch_history(
            "510300.SH",  # 沪深300ETF
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    # 仅命中 fund_etf_hist_sina, 且 symbol=sh510300
    called = fake.call_args_list
    assert len(called) == 1
    assert called[0].args[0] == "fund_etf_hist_sina"
    assert called[0].kwargs.get("symbol") == "sh510300"
    assert len(bars) == 2
    assert bars[0].close == Decimal("3.05")


@pytest.mark.asyncio
async def test_fetch_history_index_routes_to_stock_zh_index_daily():
    adapter = AShareAdapter()
    fake = _ak_dispatch({"stock_zh_index_daily": _INDEX_DAILY_DF})
    with patch("core.adapters.ashare.ak_call", fake), \
            patch("core.adapters.ashare.is_after_market_close", return_value=False):
        bars = await adapter.fetch_history(
            "000001.SH",  # 上证指数
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    called = fake.call_args_list
    assert len(called) == 1
    assert called[0].args[0] == "stock_zh_index_daily"
    assert called[0].kwargs.get("symbol") == "sh000001"
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
    fake = _ak_dispatch({"fund_etf_hist_sina": big_df})
    with patch("core.adapters.ashare.ak_call", fake):
        bars = await adapter.fetch_history(
            "510300.SH",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
    # 应该只剩 2020-06-01 和 2026-05-13,2015-01-01 被过滤掉
    assert len(bars) == 2
    assert bars[0].close == Decimal("2")
    assert bars[1].close == Decimal("5")


# ── 东财兜底: sina 日线收盘后定稿慢, 退东财补今日缺口(改动2) ──

@pytest.mark.asyncio
async def test_daily_em_fallback_stock_appends_today_when_sina_stale():
    """收盘后 sina 个股日线未覆盖今日 → 退东财 stock_zh_a_hist 补今日这根。"""
    from datetime import date as _date
    from zoneinfo import ZoneInfo
    _CN = ZoneInfo("Asia/Shanghai")
    today = datetime.now(timezone.utc).astimezone(_CN).date()
    # sina 只返回到 today-3 (未定稿今日)
    stale = today - pd.Timedelta(days=3).to_pytimedelta()
    sina_df = pd.DataFrame([
        {"date": stale, "open": 10.0, "high": 10.5, "low": 9.5,
         "close": 10.2, "volume": 1_000_000, "amount": 1e7, "turnover": 1.0},
    ])
    em_df = pd.DataFrame([
        {"日期": stale.isoformat(), "开盘": 10.0, "最高": 10.5, "最低": 9.5,
         "收盘": 10.2, "成交量": 1_000_000, "成交额": 1e7, "换手率": 1.0},
        {"日期": today.isoformat(), "开盘": 11.0, "最高": 12.0, "最低": 10.8,
         "收盘": 11.9, "成交量": 2_000_000, "成交额": 2e7, "换手率": 2.0},
    ])
    adapter = AShareAdapter()
    fake = _ak_dispatch({"stock_zh_a_daily": sina_df, "stock_zh_a_hist": em_df})
    with patch("core.adapters.ashare.ak_call", fake), \
            patch("core.adapters.ashare.is_after_market_close", return_value=True):
        bars = await adapter.fetch_history(
            "600004.SH",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime.now(timezone.utc),
        )
    # 末根 = 东财补的今日根, ts 口径 = BJT 自然日 → UTC, close=11.9
    assert bars[-1].close == Decimal("11.9")
    assert bars[-1].ts.astimezone(_CN).date() == today
    # 东财端点被调用过
    assert "stock_zh_a_hist" in [c.args[0] for c in fake.call_args_list]


@pytest.mark.asyncio
async def test_daily_em_fallback_skipped_when_not_after_close():
    """盘中(未过收盘) sina 未覆盖今日是正常的 → 不触发东财兜底。"""
    from zoneinfo import ZoneInfo
    _CN = ZoneInfo("Asia/Shanghai")
    today = datetime.now(timezone.utc).astimezone(_CN).date()
    stale = today - pd.Timedelta(days=3).to_pytimedelta()
    sina_df = pd.DataFrame([
        {"date": stale, "open": 10.0, "high": 10.5, "low": 9.5,
         "close": 10.2, "volume": 1_000_000, "amount": 1e7, "turnover": 1.0},
    ])
    adapter = AShareAdapter()
    # 东财端点未 mock: 若被调用会 raise(_ak_dispatch 语义), 断言它没被调
    fake = _ak_dispatch({"stock_zh_a_daily": sina_df})
    with patch("core.adapters.ashare.ak_call", fake), \
            patch("core.adapters.ashare.is_after_market_close", return_value=False):
        bars = await adapter.fetch_history(
            "600004.SH",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime.now(timezone.utc),
        )
    assert "stock_zh_a_hist" not in [c.args[0] for c in fake.call_args_list]
    assert bars[-1].close == Decimal("10.2")
