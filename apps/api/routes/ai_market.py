from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.deps import get_ai_market_service
from core.services.ai_market_service import AIMarketService
from core.services.market_query import RankRow

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


@router.get("/ashare/market-packet", response_model=AIPacketResponse)
async def ashare_market_packet(
    svc: AIMarketService = Depends(get_ai_market_service),
) -> AIPacketResponse:
    packet = await svc.build_ashare_packet()
    return AIPacketResponse(
        generated_at=packet.generated_at.isoformat(),
        market=packet.market,
        indices=[AIPacketSymbolDTO(**asdict(row)) for row in packet.indices],
        breadth=MarketBreadthDTO(**asdict(packet.breadth)),
        top_gainers=[_rank_dto(row) for row in packet.top_gainers],
        top_losers=[_rank_dto(row) for row in packet.top_losers],
        hot_sectors=[AIPacketSectorDTO(**asdict(row)) for row in packet.hot_sectors],
        weak_sectors=[AIPacketSectorDTO(**asdict(row)) for row in packet.weak_sectors],
        watchlist=[AIPacketSymbolDTO(**asdict(row)) for row in packet.watchlist],
        index_strength=IndexStrengthDTO(**asdict(packet.index_strength)),
        events=[AIPacketEventDTO(**asdict(row)) for row in packet.events],
        ai_brief=packet.ai_brief,
        degraded=packet.degraded,
    )


def _rank_dto(row: RankRow) -> AIPacketRankDTO:
    return AIPacketRankDTO(
        symbol=row.symbol,
        name=row.name,
        price=row.price,
        change_pct=row.change_pct,
        volume=row.volume,
        amount=row.amount,
    )
