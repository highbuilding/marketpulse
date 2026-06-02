import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.sse_hub import StreamHub, bars_key


def _entry(payload):
    return [(b"chan", [(b"1-0", {b"data": json.dumps(payload).encode()})])]


@pytest.mark.asyncio
async def test_run_dispatches_then_cancels():
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.xread = AsyncMock(side_effect=[
        _entry({"symbol": "AAPL", "interval": "5m", "final": False}),
        asyncio.CancelledError(),
    ])
    hub = StreamHub(redis, "chan", bars_key)
    sub = hub.register([("AAPL", "5m")])
    await hub.run()
    assert sub._q.qsize() == 1


@pytest.mark.asyncio
async def test_run_skips_malformed_and_continues():
    redis = MagicMock(); redis._r = MagicMock()
    bad = [(b"chan", [(b"1-0", {b"data": b"not-json"})])]
    good = _entry({"symbol": "AAPL", "interval": "5m"})
    redis._r.xread = AsyncMock(side_effect=[bad, good, asyncio.CancelledError()])
    hub = StreamHub(redis, "chan", bars_key)
    sub = hub.register([("AAPL", "5m")])
    await hub.run()
    assert sub._q.qsize() == 1


@pytest.mark.asyncio
async def test_run_retries_on_read_error(monkeypatch):
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.xread = AsyncMock(side_effect=[
        RuntimeError("redis down"),
        _entry({"symbol": "AAPL", "interval": "5m"}),
        asyncio.CancelledError(),
    ])
    monkeypatch.setattr("apps.api.sse_hub.asyncio.sleep", AsyncMock())
    hub = StreamHub(redis, "chan", bars_key)
    sub = hub.register([("AAPL", "5m")])
    await hub.run()
    assert sub._q.qsize() == 1
