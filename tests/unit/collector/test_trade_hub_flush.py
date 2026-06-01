import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.trade_hub import TradeHub
from apps.collector.us.bar_ticker import BucketTracker
from core.domain.bucket_state import BucketState


@pytest.mark.asyncio
async def test_flush_calls_writer_and_ticker_then_clears():
    writer = MagicMock(); writer.flush = AsyncMock()
    ticker = MagicMock(); ticker.publish_current = AsyncMock(); ticker.publish_provisional = AsyncMock()
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=writer, ticker=ticker)
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10,
                 ts=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    await hub._flush(now=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    writer.flush.assert_awaited_once()
    ticker.publish_current.assert_awaited_once()
    assert hub._dirty == set()


@pytest.mark.asyncio
async def test_flush_emits_provisional_for_just_closed():
    writer = MagicMock(); writer.flush = AsyncMock()
    ticker = MagicMock(); ticker.publish_current = AsyncMock(); ticker.publish_provisional = AsyncMock()
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=writer, ticker=ticker)
    hub._just_closed[("AAPL", "5m")] = BucketTracker(
        open_ts=datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
        close_ts=datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc),
        state=BucketState(open=Decimal("1"), high=Decimal("2"),
                          low=Decimal("1"), close=Decimal("2"), volume=1))
    await hub._flush(now=datetime(2026, 6, 1, 14, 36, tzinfo=timezone.utc))
    ticker.publish_provisional.assert_awaited_once()
    assert hub._just_closed == {}
