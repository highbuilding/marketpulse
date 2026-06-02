"""测试 sse_intraday._gen 使用 StreamHub 的行为。"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.sse_hub import StreamHub, intraday_key
from apps.api.routes.sse_intraday import _gen


@pytest.mark.asyncio
async def test_intraday_gen_connected_then_point_then_unregister():
    hub = StreamHub(redis=None, channel="c", key_fn=intraday_key)
    redis_cache = MagicMock()
    redis_cache.get_msgpack = AsyncMock(return_value=None)
    gen = _gen("AAPL", hub, redis_cache)
    first = await gen.__anext__()                  # connected (已 register)
    hub.dispatch({"symbol": "AAPL", "price": 1.0, "avg_price": 1.0})
    second = await gen.__anext__()                 # point
    assert b"event: connected" in first
    assert b"event: point" in second
    await gen.aclose()
    assert "AAPL" not in hub._registry             # finally 注销
