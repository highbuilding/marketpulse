"""市场附加接口 — Plan 3 Hotfix A 改:/top 走 Redis cache,api 路由 0 ak_call。

collector 的 market_top job 每 60s 预拉 → cache:market:{m}:top。本路由 GET cache,
miss 时返回 stale meta 兜底。
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.cache.redis_client import RedisCache

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/markets", tags=["markets-extras"])


class RankRowDTO(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float


class TopMeta(BaseModel):
    stale: bool = False
    reason: str | None = None
    fresh_at: str | None = None


class TopResponse(BaseModel):
    market: str
    gainers: list[RankRowDTO]
    losers: list[RankRowDTO]
    meta: TopMeta = TopMeta()


_VALID_MARKETS = {"ashare", "hk"}


@router.get("/{market}/top", response_model=TopResponse)
async def market_top(
    market: str,
    limit: int = Query(10, ge=1, le=50),
    cache: RedisCache = Depends(get_redis_cache),
) -> TopResponse:
    """A 股/港股涨跌幅榜 — 直读 Redis cache,无缓存返回 stale 兜底。"""
    if market not in _VALID_MARKETS:
        raise HTTPException(404, f"top endpoint not supported for market: {market}")

    payload = await cache.get_msgpack(keys.cache_market_top(market))
    if payload is None:
        log.info("market_top.cache_miss", market=market)
        return TopResponse(
            market=market, gainers=[], losers=[],
            meta=TopMeta(stale=True, reason="warming_up"),
        )

    gainers = [RankRowDTO(**r) for r in payload.get("gainers", [])][:limit]
    losers = [RankRowDTO(**r) for r in payload.get("losers", [])][:limit]
    fresh_at = payload.get("meta", {}).get("fresh_at")
    return TopResponse(
        market=market, gainers=gainers, losers=losers,
        meta=TopMeta(stale=False, fresh_at=fresh_at),
    )
