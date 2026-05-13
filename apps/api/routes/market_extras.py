from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.services.market_query import MarketQueryService

router = APIRouter(prefix="/api/markets", tags=["markets-extras"])
_svc = MarketQueryService()


class RankRowDTO(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float


class TopResponse(BaseModel):
    market: str
    gainers: list[RankRowDTO]
    losers: list[RankRowDTO]


class SectorRowDTO(BaseModel):
    name: str
    change_pct: float
    avg_price: float
    company_count: int
    leader_name: str
    leader_change_pct: float


class SectorsResponse(BaseModel):
    market: str
    sectors: list[SectorRowDTO]


@router.get("/{market}/top", response_model=TopResponse)
async def market_top(
    market: str,
    limit: int = Query(10, ge=1, le=50),
) -> TopResponse:
    if market == "ashare":
        gainers = await _svc.top_ashare("desc", limit)
        losers = await _svc.top_ashare("asc", limit)
    elif market == "hk":
        gainers = await _svc.top_hk("desc", limit)
        losers = await _svc.top_hk("asc", limit)
    else:
        raise HTTPException(404, f"top endpoint not supported for market: {market}")

    return TopResponse(
        market=market,
        gainers=[RankRowDTO(
            symbol=r.symbol, name=r.name, price=r.price,
            change_pct=r.change_pct, volume=r.volume, amount=r.amount,
        ) for r in gainers],
        losers=[RankRowDTO(
            symbol=r.symbol, name=r.name, price=r.price,
            change_pct=r.change_pct, volume=r.volume, amount=r.amount,
        ) for r in losers],
    )


@router.get("/ashare/sectors", response_model=SectorsResponse)
async def ashare_sectors() -> SectorsResponse:
    sectors = await _svc.sectors_ashare()
    return SectorsResponse(
        market="ashare",
        sectors=[SectorRowDTO(
            name=s.name, change_pct=s.change_pct, avg_price=s.avg_price,
            company_count=s.company_count, leader_name=s.leader_name,
            leader_change_pct=s.leader_change_pct,
        ) for s in sectors],
    )
