import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from core.domain.bucket_state import BucketState
from apps.collector.us.bar_ticker import UsBarTicker, BucketTracker


def _tracker():
    return BucketTracker(
        open_ts=datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
        close_ts=datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc),
        state=BucketState(open=Decimal("100"), high=Decimal("105"),
                          low=Decimal("99"), close=Decimal("104"), volume=500),
    )


@pytest.mark.asyncio
async def test_publish_current_final_false_and_writes_current():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    t = UsBarTicker(redis)
    await t.publish_current("AAPL", "5m", _tracker())
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is False
    assert payload["interval"] == "5m" and payload["symbol"] == "AAPL"
    assert payload["ts"].startswith("2026-06-01T14:35")
    assert redis.set_msgpack.await_count == 1


@pytest.mark.asyncio
async def test_publish_provisional_final_true_bus_only():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    t = UsBarTicker(redis)
    await t.publish_provisional("AAPL", "5m", _tracker())
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is True
    assert redis.set_msgpack.await_count == 0
