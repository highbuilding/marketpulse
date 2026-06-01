import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.routes.sse_bars import _register_subscriptions


@pytest.mark.asyncio
async def test_register_subscriptions_setex_per_symbol():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.set = AsyncMock()
    await _register_subscriptions({"AAPL", "600519.SH"}, "5m", redis)
    assert redis._r.set.await_count == 2
    keys_set = [c.args[0] for c in redis._r.set.await_args_list]
    assert "state:subscribe:us:AAPL:5m" in keys_set
    assert "state:subscribe:ashare:600519.SH:5m" in keys_set
    for c in redis._r.set.await_args_list:
        assert c.kwargs.get("ex") == 120


@pytest.mark.asyncio
async def test_register_subscriptions_swallows_errors():
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.set = AsyncMock(side_effect=RuntimeError("boom"))
    # 不应抛出 (优雅降级)
    await _register_subscriptions({"AAPL"}, "5m", redis)
