from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
