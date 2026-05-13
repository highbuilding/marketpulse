from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import get_quote_cache, get_registry
from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache

router = APIRouter(prefix="/api/markets", tags=["markets"])


class QuoteDTO(BaseModel):
    symbol: str
    price: float
    change_pct: float
    volume: int
    source: str
    ts: str


class OverviewResponse(BaseModel):
    market: str
    status: str
    quotes: list[QuoteDTO]
    top_gainers: list[QuoteDTO]
    top_losers: list[QuoteDTO]
    indices: list[QuoteDTO]


@router.get("/{market}/overview", response_model=OverviewResponse)
async def overview(
    market: str,
    registry: AdapterRegistry = Depends(get_registry),
    cache: QuoteCache = Depends(get_quote_cache),
) -> OverviewResponse:
    if market not in registry.markets():
        raise HTTPException(status_code=404, detail=f"unknown market: {market}")
    snap = cache.snapshot(market)
    dtos = [QuoteDTO(
        symbol=q.symbol, price=float(q.price), change_pct=q.change_pct,
        volume=q.volume, source=q.source, ts=q.ts.isoformat(),
    ) for q in snap]

    if not dtos:
        return OverviewResponse(
            market=market, status="warming",
            quotes=[], top_gainers=[], top_losers=[], indices=[],
        )

    index_set = set(registry.index_symbols(market))
    indices = [d for d in dtos if d.symbol in index_set]
    stocks = [d for d in dtos if d.symbol not in index_set]
    gainers = sorted(stocks, key=lambda x: x.change_pct, reverse=True)[:10]
    losers = sorted(stocks, key=lambda x: x.change_pct)[:10]
    return OverviewResponse(
        market=market, status="ok",
        quotes=dtos, top_gainers=gainers, top_losers=losers, indices=indices,
    )
