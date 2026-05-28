from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import fakeredis.aioredis

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.market_metrics.market_width import MarketBreadthMetrics
from core.market_metrics.index_strength import IndexStrengthMetrics
from core.services.ai_market_service import AIPacket
from apps.collector.jobs.ai_packet import packet_to_dict, refresh_ai_packet


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


def _empty_packet() -> AIPacket:
    return AIPacket(
        generated_at=datetime(2026, 5, 28, 1, 0, tzinfo=timezone.utc),
        market="ashare",
        indices=[],
        breadth=MarketBreadthMetrics(
            total=0, advancers=0, decliners=0, flat=0,
            up_limit=0, down_limit=0, total_amount=0.0,
            up_ratio=0.0, down_ratio=0.0, net_width=0,
        ),
        top_gainers=[], top_losers=[],
        hot_sectors=[], weak_sectors=[],
        watchlist=[],
        index_strength=IndexStrengthMetrics(
            ranking=[], small_vs_large_pct=None, growth_vs_large_pct=None,
        ),
        events=[],
        ai_brief={},
        degraded=[],
    )


def test_packet_to_dict_basic_shape():
    p = _empty_packet()
    d = packet_to_dict(p)
    assert d["market"] == "ashare"
    assert d["generated_at"] == "2026-05-28T01:00:00+00:00"
    for k in ("indices", "top_gainers", "top_losers", "hot_sectors",
              "weak_sectors", "watchlist", "events", "degraded"):
        assert isinstance(d[k], list)
    for k in ("breadth", "index_strength", "ai_brief"):
        assert isinstance(d[k], dict)


async def test_refresh_writes_cache(cache):
    svc = MagicMock()
    svc.build_ashare_packet = AsyncMock(return_value=_empty_packet())
    await refresh_ai_packet(svc=svc, cache=cache)
    payload = await cache.get_msgpack(keys.cache_market_ai_packet("ashare"))
    assert payload is not None
    assert payload["market"] == "ashare"
    assert payload["meta"]["stale"] is False
    assert "fresh_at" in payload["meta"]


async def test_refresh_swallows_build_failure(cache):
    svc = MagicMock()
    svc.build_ashare_packet = AsyncMock(side_effect=RuntimeError("ak timeout"))
    await refresh_ai_packet(svc=svc, cache=cache)  # 不抛
    payload = await cache.get_msgpack(keys.cache_market_ai_packet("ashare"))
    assert payload is None  # 失败不写 cache,沿用旧 cache(若仍在 TTL 内)
