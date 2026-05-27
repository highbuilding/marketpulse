"""市场 dashboard 聚合接口 — 一次返回前端 /market 页所需。

collector 的 market_dashboard job 预先写好 cache:market:{m}:dashboard,
本路由直读 Redis,不调 ak_call/不查 DB。
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.cache.redis_client import RedisCache

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/markets", tags=["dashboard"])

_VALID_MARKETS = {"ashare", "hk", "us", "crypto"}


@router.get("/{market}/dashboard")
async def dashboard(
    market: str = Path(..., min_length=2, max_length=10),
    cache: RedisCache = Depends(get_redis_cache),
) -> dict[str, Any]:
    if market not in _VALID_MARKETS:
        raise HTTPException(404, f"unknown market: {market}")
    payload = await cache.get_msgpack(keys.cache_market_dashboard(market))
    if payload is None:
        return {
            "market": market, "indices": [],
            "overview": None, "north_flow": None, "hot_sectors": None,
            "meta": {"stale": True, "reason": "warming_up"},
        }
    return payload
