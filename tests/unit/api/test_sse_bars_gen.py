"""测试 _stream_gen 改用 hub 后的行为。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.sse_hub import StreamHub, bars_key
from apps.api.routes.sse_bars import _stream_gen


@pytest.mark.asyncio
async def test_stream_gen_yields_connected_then_tick_then_unregisters():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    redis_cache = MagicMock()
    redis_cache.get_msgpack = AsyncMock(return_value=None)   # 无 init 快照
    gen = _stream_gen({"AAPL"}, "5m", hub, redis_cache)
    first = await gen.__anext__()           # connected (此时已 register)
    hub.dispatch({"symbol": "AAPL", "interval": "5m", "final": False, "close": 1})
    second = await gen.__anext__()          # 应为 tick
    assert b"event: connected" in first
    assert b"event: tick" in second
    await gen.aclose()
    assert ("AAPL", "5m") not in hub._registry   # finally 注销
