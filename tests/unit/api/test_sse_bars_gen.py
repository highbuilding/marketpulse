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


@pytest.mark.asyncio
async def test_register_refreshed_on_message_path(monkeypatch):
    """活跃推送(消息不断)也要周期续期订阅, 否则 TTL 过期 collector 停实时。"""
    import apps.api.routes.sse_bars as m
    monkeypatch.setattr(m, "_REGISTER_REFRESH_S", 0)   # 每条消息都到续期点
    calls = []

    async def fake_reg(symbols, interval, rc):
        calls.append(1)

    monkeypatch.setattr(m, "_register_subscriptions", fake_reg)
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    rc = MagicMock(); rc.get_msgpack = AsyncMock(return_value=None)
    gen = m._stream_gen({"AAPL"}, "5m", hub, rc)
    await gen.__anext__()                       # connected → reg #1
    hub.dispatch({"symbol": "AAPL", "interval": "5m", "final": False})
    await gen.__anext__()                       # message 路径 → reg #2
    await gen.aclose()
    assert len(calls) >= 2
