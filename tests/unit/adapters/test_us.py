from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.adapters.us import USAdapter, _to_yfinance_ticker


@pytest.mark.asyncio
async def test_us_adapter_disabled_without_key(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    adapter = USAdapter()
    assert adapter.has_primary is False
    h = await adapter.health()
    assert h.state in {"degraded", "disabled"}


@pytest.mark.asyncio
async def test_us_adapter_uses_alpaca_when_key_present(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    adapter = USAdapter()
    assert adapter.has_primary is True

    with patch.object(adapter, "_fetch_snapshot_alpaca", return_value=[
        SimpleNamespace(symbol="AAPL", price=Decimal("192.0"), change_pct=0.5,
                        volume=100, source="alpaca",
                        market="us", ts=datetime.now(timezone.utc))
    ]) as m:
        quotes = await adapter.fetch_snapshot(["AAPL"])
    assert m.called
    assert quotes[0].source == "alpaca"


@pytest.mark.asyncio
async def test_us_falls_back_to_yfinance_on_alpaca_error(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    adapter = USAdapter()
    with patch.object(adapter, "_fetch_snapshot_alpaca", side_effect=RuntimeError("429")), \
         patch.object(adapter, "_fetch_snapshot_yfinance") as yf_mock:
        yf_mock.return_value = []
        await adapter.fetch_snapshot(["AAPL"])
    assert yf_mock.called


# ── _to_yfinance_ticker ─────────────────────────────────────────────────────


def test_to_yfinance_ticker_class_share():
    assert _to_yfinance_ticker("BRK.B") == "BRK-B"
    assert _to_yfinance_ticker("BF.A") == "BF-A"


def test_to_yfinance_ticker_plain():
    assert _to_yfinance_ticker("AAPL") == "AAPL"
    assert _to_yfinance_ticker("SPY") == "SPY"


# ── fetch_intraday ──────────────────────────────────────────────────────────


def _mock_intraday_df():
    """yfinance.download intraday 返回 ET 时区的 DataFrame。"""
    idx = pd.DatetimeIndex(
        ["2026-05-15 09:30:00-04:00", "2026-05-15 10:30:00-04:00"],
        tz="America/New_York",
    )
    return pd.DataFrame({
        "Open":   [180.0, 181.0],
        "High":   [181.0, 182.0],
        "Low":    [179.0, 180.5],
        "Close":  [180.5, 181.5],
        "Volume": [100000, 120000],
    }, index=idx)


@pytest.mark.asyncio
async def test_fetch_intraday_basic():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 2
    assert bars[0].symbol == "AAPL"
    assert bars[0].market == "us"
    assert bars[0].interval == "60m"
    # 13:30 UTC == 09:30 EDT
    assert bars[0].ts == datetime(2026, 5, 15, 13, 30, tzinfo=timezone.utc)
    assert bars[0].open == Decimal("180.0")


@pytest.mark.asyncio
async def test_fetch_intraday_class_share_converts_ticker():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        bars = await adapter.fetch_intraday("BRK.B", freq="60")
    # yfinance.download 应该被以 'BRK-B' 调用(adapter 内部转换)
    call = mock_yf.download.call_args
    assert call.args[0] == "BRK-B"
    # business 层 Bar 仍标 BRK.B
    assert bars[0].symbol == "BRK.B"


@pytest.mark.asyncio
async def test_fetch_intraday_drops_nan():
    df = _mock_intraday_df().copy()
    df.iloc[0, df.columns.get_loc("Close")] = float("nan")
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=df)
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 1  # 第一行 NaN 被丢弃


@pytest.mark.asyncio
async def test_fetch_intraday_drops_high_low_nan():
    """High 或 Low NaN 时也要丢弃,避免 Decimal('nan')。"""
    df = _mock_intraday_df().copy()
    df.iloc[0, df.columns.get_loc("High")] = float("nan")
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=df)
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 1  # 第一行 High NaN, 被丢弃


@pytest.mark.asyncio
async def test_fetch_intraday_period_mapping():
    """1m freq → period=7d, 其他 → 60d, prepost 始终 True。"""
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        await adapter.fetch_intraday("AAPL", freq="1")
    assert mock_yf.download.call_args.kwargs["period"] == "7d"
    assert mock_yf.download.call_args.kwargs["interval"] == "1m"
    assert mock_yf.download.call_args.kwargs["prepost"] is True

    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        await adapter.fetch_intraday("AAPL", freq="60")
    assert mock_yf.download.call_args.kwargs["period"] == "60d"
    assert mock_yf.download.call_args.kwargs["interval"] == "60m"
    assert mock_yf.download.call_args.kwargs["prepost"] is True


# ── fetch_history ───────────────────────────────────────────────────────────


def _mock_history_df():
    """yfinance.download(start, end) 在 1d 模式返回 naive index, 类型 DatetimeIndex。"""
    idx = pd.DatetimeIndex(["2026-05-15", "2026-05-16"])
    return pd.DataFrame({
        "Open":   [200.0, 201.0],
        "High":   [202.0, 203.0],
        "Low":    [199.0, 200.0],
        "Close":  [201.0, 202.0],
        "Volume": [1000000, 900000],
    }, index=idx)


@pytest.mark.asyncio
async def test_fetch_history_normalizes_to_et_midnight():
    """1d ts 必须 normalize 为 ET 自然交易日 00:00 → UTC。
    2026-05-15 00:00 ET (EDT, UTC-4) → 2026-05-15 04:00 UTC。"""
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_history_df())
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    assert bars[0].ts == datetime(2026, 5, 15, 4, 0, tzinfo=timezone.utc)
    assert bars[1].ts == datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc)
    assert bars[0].interval == "1d"
    assert bars[0].market == "us"


@pytest.mark.asyncio
async def test_fetch_history_class_share():
    """业务层 BRK.B → yfinance 收到 BRK-B, Bar.symbol 仍 BRK.B。"""
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_history_df())
        bars = await adapter.fetch_history(
            "BRK.B",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert mock_yf.download.call_args.args[0] == "BRK-B"
    assert bars[0].symbol == "BRK.B"
    assert mock_yf.download.call_args.kwargs["auto_adjust"] is False


@pytest.mark.asyncio
async def test_fetch_history_winter_est_offset():
    """EST(UTC-5,冬令时)下,2026-01-15 ET 00:00 → 2026-01-15 05:00 UTC。"""
    idx = pd.DatetimeIndex(["2026-01-15"])
    df = pd.DataFrame({
        "Open": [200.0], "High": [202.0], "Low": [199.0],
        "Close": [201.0], "Volume": [1000000],
    }, index=idx)
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
    assert bars[0].ts == datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)


# ── verify_ticker ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_ticker_valid():
    adapter = USAdapter()
    fake_info = MagicMock(last_price=180.0)
    fake_ticker = MagicMock(
        fast_info=fake_info,
        info={"longName": "Apple Inc."},
    )
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(return_value=fake_ticker)
        ok, name = await adapter.verify_ticker("AAPL")
    assert ok is True
    assert name == "Apple Inc."


@pytest.mark.asyncio
async def test_verify_ticker_zero_price_valid():
    """last_price=0.0 是合法的(极低价 / 停牌当日),不应误判为无效。"""
    adapter = USAdapter()
    fake_info = MagicMock(last_price=0.0)
    fake_ticker = MagicMock(
        fast_info=fake_info,
        info={"longName": "Some Penny Stock"},
    )
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(return_value=fake_ticker)
        ok, name = await adapter.verify_ticker("PENNY")
    assert ok is True
    assert name == "Some Penny Stock"


@pytest.mark.asyncio
async def test_verify_ticker_unknown():
    adapter = USAdapter()
    fake_info = MagicMock(last_price=None)
    fake_ticker = MagicMock(fast_info=fake_info, info={})
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(return_value=fake_ticker)
        ok, name = await adapter.verify_ticker("ZZZZZZ")
    assert ok is False
    assert name is None


@pytest.mark.asyncio
async def test_verify_ticker_exception_returns_false():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(side_effect=RuntimeError("network"))
        ok, name = await adapter.verify_ticker("AAPL")
    assert ok is False
    assert name is None


def test_us_adapter_has_backup_cb_with_strict_params():
    """yfinance backup 必须有独立 CircuitBreaker, 比 primary 更激进。"""
    adapter = USAdapter()
    assert hasattr(adapter, "backup_cb")
    assert adapter.backup_cb.fail_threshold == 2
    assert adapter.backup_cb.reset_after_s == 1800
    # 与 primary 是独立实例
    assert adapter.backup_cb is not adapter.primary_cb


def test_us_adapter_accepts_dir_repo_optional():
    """dir_repo 可选注入, 不传时 akshare 路径不可用(向后兼容)。"""
    adapter = USAdapter()
    assert adapter.dir_repo is None


# ── _resolve_akshare_code ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_akshare_code_cached():
    """已缓存时直接返回, 不调 ak_call。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    with patch("core.adapters.us.ak_call") as mock_ak:
        result = await adapter._resolve_akshare_code("AAPL")
    assert result == "105.AAPL"
    mock_ak.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_akshare_code_probes_105_first():
    """未缓存 → 试 105.X, 命中后回写。"""
    import pandas as pd
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value=None)
    fake_repo.set_akshare_code = AsyncMock()
    adapter = USAdapter(dir_repo=fake_repo)
    fake_df = pd.DataFrame({"日期": ["2026-01-02"], "开盘": [180.0],
                            "收盘": [181.0], "最高": [181.5], "最低": [179.5],
                            "成交量": [1000000]})
    with patch("core.adapters.us.ak_call", new=AsyncMock(return_value=fake_df)) as mock_ak:
        result = await adapter._resolve_akshare_code("AAPL")
    assert result == "105.AAPL"
    fake_repo.set_akshare_code.assert_awaited_once_with("AAPL", "105.AAPL")
    assert mock_ak.await_count == 1


@pytest.mark.asyncio
async def test_resolve_akshare_code_falls_back_106():
    """105 抛异常 → 试 106 → 命中。"""
    import pandas as pd
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value=None)
    fake_repo.set_akshare_code = AsyncMock()
    adapter = USAdapter(dir_repo=fake_repo)
    fake_df = pd.DataFrame({"日期": ["2026-01-02"], "开盘": [200.0],
                            "收盘": [201.0], "最高": [202.0], "最低": [199.0],
                            "成交量": [500000]})

    async def fake_ak_call(func_name, *args, **kwargs):
        if "105." in kwargs.get("symbol", ""):
            raise RuntimeError("not found")
        return fake_df

    with patch("core.adapters.us.ak_call", side_effect=fake_ak_call):
        result = await adapter._resolve_akshare_code("XYZ")
    assert result == "106.XYZ"
    fake_repo.set_akshare_code.assert_awaited_once_with("XYZ", "106.XYZ")


@pytest.mark.asyncio
async def test_resolve_akshare_code_all_fail_returns_none():
    """三种前缀全失败 → None, 不写库。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value=None)
    fake_repo.set_akshare_code = AsyncMock()
    adapter = USAdapter(dir_repo=fake_repo)
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("not found")):
        result = await adapter._resolve_akshare_code("ZZZZ")
    assert result is None
    fake_repo.set_akshare_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_akshare_code_no_repo_returns_none():
    """没注入 dir_repo → 直接返 None。"""
    adapter = USAdapter()  # dir_repo=None
    result = await adapter._resolve_akshare_code("AAPL")
    assert result is None


# ── fetch_history 路由 (akshare 主 / yfinance 备) ──────────────────────────


@pytest.mark.asyncio
async def test_fetch_history_uses_akshare_when_resolved():
    """akshare 主源命中时返回 akshare 数据,不调 yfinance。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    fake_df = pd.DataFrame({
        "日期": ["2026-05-19"], "开盘": [296.97], "收盘": [298.97],
        "最高": [300.51], "最低": [296.35], "成交量": [42243561],
    })
    with patch("core.adapters.us.ak_call", new=AsyncMock(return_value=fake_df)) as mock_ak, \
         patch("core.adapters.us.yf") as mock_yf:
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    mock_yf.download.assert_not_called()
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].market == "us"
    assert bars[0].interval == "1d"
    # 5/19 ET 00:00 EDT (UTC-4) → UTC 5/19 04:00
    assert bars[0].ts == datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc)
    assert bars[0].close == Decimal("298.97")


@pytest.mark.asyncio
async def test_fetch_history_falls_back_to_yfinance_when_akshare_fails():
    """akshare 抛 → fallback yfinance(backup_cb 未熔断时)。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    yf_df = _mock_history_df()
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("akshare network")), \
         patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    mock_yf.download.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_history_raises_when_yfinance_circuit_open():
    """akshare 失败 + yfinance backup_cb 已熔断 → AdapterError。"""
    from core.adapters.base import AdapterError
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    # 强制熔断
    adapter.backup_cb.state = "open"
    adapter.backup_cb.opened_at = 9999999999.0  # 远未来, 不会自动 half-open
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("akshare fail")):
        with pytest.raises(AdapterError, match="circuit open"):
            await adapter.fetch_history(
                "AAPL",
                datetime(2026, 5, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 20, tzinfo=timezone.utc),
            )


@pytest.mark.asyncio
async def test_fetch_history_yfinance_failure_records_backup_cb():
    """akshare 抛 → yfinance 抛 → backup_cb.failure_count 增加。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    initial = adapter.backup_cb.failure_count
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("akshare")), \
         patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(side_effect=RuntimeError("yfinance 429"))
        with pytest.raises(Exception):
            await adapter.fetch_history(
                "AAPL",
                datetime(2026, 5, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 20, tzinfo=timezone.utc),
            )
    assert adapter.backup_cb.failure_count == initial + 1


@pytest.mark.asyncio
async def test_fetch_history_no_dir_repo_skips_to_yfinance():
    """没 dir_repo → akshare 路径返空 → 走 yfinance。"""
    adapter = USAdapter()  # 不注入 dir_repo
    yf_df = _mock_history_df()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    mock_yf.download.assert_called_once()
