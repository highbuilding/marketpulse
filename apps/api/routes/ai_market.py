from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.cache.redis_client import RedisCache
from core.services.market_query import RankRow

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai-market"])


class MarketBreadthDTO(BaseModel):
    total: int
    advancers: int
    decliners: int
    flat: int
    up_limit: int
    down_limit: int
    total_amount: float
    up_ratio: float
    down_ratio: float
    net_width: int


class AIPacketEventDTO(BaseModel):
    level: str
    category: str
    title: str
    detail: str
    symbols: list[str]
    score: float


class AIPacketSymbolDTO(BaseModel):
    symbol: str
    name: str | None
    price: float | None
    change_pct: float | None
    volume: int | None
    sectors: list[str]


class AIPacketRankDTO(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float


class AIPacketSectorDTO(BaseModel):
    code: str
    name: str
    change_pct: float
    company_count: int
    leader_name: str
    leader_change_pct: float
    leader_symbol: str | None = None
    main_net: float | None = None
    constituents: list[AIPacketSymbolDTO] | None = None
    up_count: int | None = None
    down_count: int | None = None
    up_ratio: float | None = None
    avg_change_pct: float | None = None
    leader_dominance_pct: float | None = None
    breadth_label: str


class IndexStrengthDTO(BaseModel):
    ranking: list[dict[str, Any]]
    small_vs_large_pct: float | None
    growth_vs_large_pct: float | None


class AIPacketMeta(BaseModel):
    stale: bool = False
    reason: str | None = None
    fresh_at: str | None = None


class AIPacketResponse(BaseModel):
    generated_at: str
    market: str
    indices: list[AIPacketSymbolDTO]
    breadth: MarketBreadthDTO
    top_gainers: list[AIPacketRankDTO]
    top_losers: list[AIPacketRankDTO]
    hot_sectors: list[AIPacketSectorDTO]
    weak_sectors: list[AIPacketSectorDTO]
    watchlist: list[AIPacketSymbolDTO]
    index_strength: IndexStrengthDTO
    events: list[AIPacketEventDTO]
    ai_brief: dict[str, Any]
    degraded: list[str]
    meta: AIPacketMeta = AIPacketMeta()


def _empty_response(market: str, reason: str) -> AIPacketResponse:
    return AIPacketResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market,
        indices=[],
        breadth=MarketBreadthDTO(
            total=0, advancers=0, decliners=0, flat=0,
            up_limit=0, down_limit=0, total_amount=0.0,
            up_ratio=0.0, down_ratio=0.0, net_width=0,
        ),
        top_gainers=[], top_losers=[],
        hot_sectors=[], weak_sectors=[],
        watchlist=[],
        index_strength=IndexStrengthDTO(
            ranking=[], small_vs_large_pct=None, growth_vs_large_pct=None,
        ),
        events=[], ai_brief={}, degraded=[],
        meta=AIPacketMeta(stale=True, reason=reason),
    )


@router.get("/ashare/market-packet", response_model=AIPacketResponse)
async def ashare_market_packet(
    cache: RedisCache = Depends(get_redis_cache),
) -> AIPacketResponse:
    """A 股 AI 大盘数据包 — 直读 Redis cache,无缓存返回 stale 兜底。"""
    payload = await cache.get_msgpack(keys.cache_market_ai_packet("ashare"))
    if payload is None:
        log.info("ai_packet.cache_miss", market="ashare")
        return _empty_response("ashare", "warming_up")

    meta_in = payload.get("meta", {})
    return AIPacketResponse(
        generated_at=payload["generated_at"],
        market=payload["market"],
        indices=[AIPacketSymbolDTO(**row) for row in payload.get("indices", [])],
        breadth=MarketBreadthDTO(**payload["breadth"]),
        top_gainers=[AIPacketRankDTO(**row) for row in payload.get("top_gainers", [])],
        top_losers=[AIPacketRankDTO(**row) for row in payload.get("top_losers", [])],
        hot_sectors=[AIPacketSectorDTO(**row) for row in payload.get("hot_sectors", [])],
        weak_sectors=[AIPacketSectorDTO(**row) for row in payload.get("weak_sectors", [])],
        watchlist=[AIPacketSymbolDTO(**row) for row in payload.get("watchlist", [])],
        index_strength=IndexStrengthDTO(**payload["index_strength"]),
        events=[AIPacketEventDTO(**row) for row in payload.get("events", [])],
        ai_brief=payload.get("ai_brief", {}),
        degraded=payload.get("degraded", []),
        meta=AIPacketMeta(stale=False, fresh_at=meta_in.get("fresh_at")),
    )
