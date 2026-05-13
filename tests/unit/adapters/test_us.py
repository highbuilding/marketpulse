from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.adapters.us import USAdapter


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
