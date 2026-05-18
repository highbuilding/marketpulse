from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.models import Bar
from core.services.kline_service import KLineService


def _bar(symbol: str, market: str, ts: datetime) -> Bar:
    return Bar(
        market=market, symbol=symbol, ts=ts,
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
        close=Decimal("1"), volume=0, interval="1d",
    )


@pytest.mark.asyncio
async def test_routes_us_symbol_to_us_adapter():
    repo = MagicMock()
    repo.fetch_history = MagicMock(return_value=[])
    repo.insert_bars = MagicMock()
    us_adapter = MagicMock()
    us_adapter.fetch_history = AsyncMock(return_value=[
        _bar("AAPL", "us", datetime(2026, 5, 15, 4, 0, tzinfo=timezone.utc)),
    ])
    ashare_adapter = MagicMock()
    ashare_adapter.fetch_history = AsyncMock()

    svc = KLineService(repo, {"us": us_adapter, "ashare": ashare_adapter})
    bars = await svc.get_bars(
        "AAPL", interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    us_adapter.fetch_history.assert_called_once()
    ashare_adapter.fetch_history.assert_not_called()
    assert bars[0].symbol == "AAPL"
    # 查缓存时按 us
    repo.fetch_history.assert_called_with(
        "us", "AAPL", datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 20, tzinfo=timezone.utc), interval="1d",
    )


@pytest.mark.asyncio
async def test_routes_ashare_symbol_to_ashare_adapter():
    repo = MagicMock()
    repo.fetch_history = MagicMock(return_value=[])
    repo.insert_bars = MagicMock()
    us_adapter = MagicMock()
    us_adapter.fetch_history = AsyncMock()
    ashare_adapter = MagicMock()
    ashare_adapter.fetch_history = AsyncMock(return_value=[
        _bar("600519.SH", "ashare", datetime(2026, 5, 14, 16, tzinfo=timezone.utc)),
    ])

    svc = KLineService(repo, {"us": us_adapter, "ashare": ashare_adapter})
    await svc.get_bars(
        "600519.SH", interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    ashare_adapter.fetch_history.assert_called_once()
    us_adapter.fetch_history.assert_not_called()


@pytest.mark.asyncio
async def test_raises_when_no_adapter_for_market():
    repo = MagicMock()
    repo.fetch_history = MagicMock(return_value=[])
    svc = KLineService(repo, {"ashare": MagicMock()})
    with pytest.raises(ValueError, match="no adapter for market=us"):
        await svc.get_bars(
            "AAPL", interval="1d",
            start=datetime(2026, 5, 1, tzinfo=timezone.utc),
            end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
