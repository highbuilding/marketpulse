"""bars_history Redis 页缓存单测。

验证:
- 缓存命中时直接返回,不再调 collector (client.get 不被调用)
- 缓存未命中时转发 collector,成功后写入缓存
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_history_cache_hit_skips_collector():
    from apps.api.routes import symbols as m
    rc = MagicMock(); rc._r = MagicMock()
    rc._r.get = AsyncMock(return_value=json.dumps({"bars": [], "meta": {"stale": False}}).encode())
    client = MagicMock(); client.get = AsyncMock()
    await m.bars_history("AAPL", interval="5m", before="2026-06-01T00:00:00",
                         limit=500, client=client, redis_cache=rc)
    client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_cache_miss_forwards_and_caches():
    from apps.api.routes import symbols as m
    rc = MagicMock(); rc._r = MagicMock()
    rc._r.get = AsyncMock(return_value=None); rc._r.set = AsyncMock()
    resp = MagicMock(); resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"bars": [], "meta": {}})
    client = MagicMock(); client.get = AsyncMock(return_value=resp)
    await m.bars_history("AAPL", interval="5m", before="2026-06-01T00:00:00",
                         limit=500, client=client, redis_cache=rc)
    client.get.assert_awaited_once()
    rc._r.set.assert_awaited()
