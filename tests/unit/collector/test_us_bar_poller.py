import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from core.domain.models import Bar
from apps.collector.us.bar_poller import UsBarPoller


def _bar(ts, interval="5m"):
    return Bar(market="us", symbol="AAPL", ts=ts, open=Decimal("1"), high=Decimal("2"),
               low=Decimal("1"), close=Decimal("2"), volume=10, interval=interval)


@pytest.mark.asyncio
async def test_poll_upserts_new_closed_and_publishes(monkeypatch):
    repo = MagicMock()
    repo.fetch_history_paged.return_value = []
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    adapter = MagicMock()
    closed = _bar(datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc))
    adapter.fetch_intraday = AsyncMock(return_value=[closed])
    poller = UsBarPoller(repo, redis, adapter)
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.us.bar_poller.aggregate_and_publish", agg)
    await poller.poll_one("AAPL", "5m")
    repo.insert_bars.assert_called_once()
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is True and payload["interval"] == "5m"
    agg.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_skips_already_stored(monkeypatch):
    stored = _bar(datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc))
    repo = MagicMock()
    repo.fetch_history_paged.return_value = [stored]
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[stored])
    poller = UsBarPoller(repo, redis, adapter)
    monkeypatch.setattr("apps.collector.us.bar_poller.aggregate_and_publish", AsyncMock())
    await poller.poll_one("AAPL", "5m")
    repo.insert_bars.assert_not_called()
    assert redis._r.xadd.await_count == 0
