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


# ── fetch_intraday (Alpaca IEX) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_intraday_uses_alpaca():
    """has_primary=True → fetch_intraday 走 Alpaca。"""
    adapter = USAdapter()
    adapter.has_primary = True
    fake_bars = [
        _mock_alpaca_bar(
            datetime(2026, 5, 20, 13, 30, tzinfo=timezone.utc),
            300.0, 301.0, 299.5, 300.8, 50000,
        ),
        _mock_alpaca_bar(
            datetime(2026, 5, 20, 14, 30, tzinfo=timezone.utc),
            300.8, 302.0, 300.5, 301.5, 60000,
        ),
    ]
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": fake_bars}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 2
    assert bars[0].interval == "60m"
    assert bars[0].ts == datetime(2026, 5, 20, 13, 30, tzinfo=timezone.utc)
    assert bars[1].close == Decimal("301.5")


@pytest.mark.asyncio
async def test_fetch_intraday_alpaca_5m_freq():
    """5m freq 调用 TimeFrame(5, Minute)。"""
    from alpaca.data.timeframe import TimeFrameUnit
    adapter = USAdapter()
    adapter.has_primary = True
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": []}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        await adapter.fetch_intraday("AAPL", freq="5")
    call = fake_client.get_stock_bars.call_args
    tf = call.args[0].timeframe
    assert tf.amount == 5
    assert tf.unit == TimeFrameUnit.Minute


@pytest.mark.asyncio
async def test_fetch_intraday_invalid_freq_raises():
    adapter = USAdapter()
    with pytest.raises(ValueError, match="unsupported freq"):
        await adapter.fetch_intraday("AAPL", freq="2")


@pytest.mark.asyncio
async def test_fetch_intraday_no_alpaca_raises():
    """has_primary=False → 抛 AdapterError。"""
    from core.adapters.base import AdapterError
    adapter = USAdapter()
    adapter.has_primary = False
    with pytest.raises(AdapterError, match="alpaca not configured"):
        await adapter.fetch_intraday("AAPL", freq="60")


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
    """yfinance fallback 路径 1d ts 必须 normalize 为 ET 自然交易日 00:00 → UTC。
    2026-05-15 00:00 ET (EDT, UTC-4) → 2026-05-15 04:00 UTC。"""
    adapter = USAdapter()
    adapter.has_primary = False  # 强制走 yfinance
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
    adapter.has_primary = False  # 强制走 yfinance
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
    adapter.has_primary = False  # 强制走 yfinance
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


@pytest.mark.asyncio
async def test_fetch_snapshot_skips_yfinance_when_circuit_open():
    """Alpaca 失败 + yfinance backup_cb 已熔断 → 静默返空,不抛。"""
    adapter = USAdapter()
    # 设没 alpaca + yfinance 熔断
    adapter.has_primary = False
    adapter.backup_cb.state = "open"
    adapter.backup_cb.opened_at = 9999999999.0
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock()
        result = await adapter.fetch_snapshot(["AAPL"])
    assert result == []
    mock_yf.Ticker.assert_not_called()


# ── fetch_history Alpaca 路径 ───────────────────────────────────────────────


def _mock_alpaca_bar(timestamp, open_, high, low, close, volume):
    """模拟 Alpaca SDK 返回的 Bar 对象。"""
    bar = MagicMock()
    bar.timestamp = timestamp
    bar.open = open_
    bar.high = high
    bar.low = low
    bar.close = close
    bar.volume = volume
    return bar


@pytest.mark.asyncio
async def test_fetch_history_uses_alpaca_when_configured():
    """has_primary=True → 走 Alpaca, 不调 yfinance。"""
    adapter = USAdapter()
    adapter.has_primary = True
    fake_bars = [
        _mock_alpaca_bar(
            datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
            296.97, 300.51, 296.35, 298.97, 42243561,
        ),
    ]
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": fake_bars}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client), \
         patch("core.adapters.us.yf") as mock_yf:
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].market == "us"
    assert bars[0].interval == "1d"
    assert bars[0].ts == datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc)
    assert bars[0].close == Decimal("298.97")
    mock_yf.download.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_history_alpaca_failure_falls_back_yfinance():
    """Alpaca 抛 → fallback yfinance(backup_cb 未熔断时)。"""
    adapter = USAdapter()
    adapter.has_primary = True
    yf_df = _mock_history_df()
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               side_effect=RuntimeError("alpaca network")), \
         patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2  # _mock_history_df 返 2 行
    mock_yf.download.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_history_no_alpaca_falls_back_yfinance():
    """has_primary=False(无 key)→ 直接 yfinance。"""
    adapter = USAdapter()
    adapter.has_primary = False
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


@pytest.mark.asyncio
async def test_fetch_history_class_share_uses_dash():
    """BRK.B → Alpaca 拿 BRK-B, Bar.symbol 仍 BRK.B。"""
    adapter = USAdapter()
    adapter.has_primary = True
    fake_bars = [_mock_alpaca_bar(
        datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
        500.0, 510.0, 495.0, 505.0, 1000000,
    )]
    fake_resp = MagicMock()
    fake_resp.data = {"BRK-B": fake_bars}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        bars = await adapter.fetch_history(
            "BRK.B",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert bars[0].symbol == "BRK.B"
    call = fake_client.get_stock_bars.call_args
    assert call.args[0].symbol_or_symbols == "BRK-B"
