from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import (
    get_fund_flow_service, get_kline_service, get_symbol_directory_service,
)
from core.services.fund_flow_service import FundFlowService
from core.services.kline_service import KLineService
from core.services.symbol_directory_service import SymbolDirectoryService

router = APIRouter(prefix="/api/symbols", tags=["symbols"])

_VALID_INTERVALS = {"1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m"}


class BarDTO(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarsResponse(BaseModel):
    symbol: str
    interval: str
    bars: list[BarDTO]


class FundFlowRowDTO(BaseModel):
    ts: str
    main_net: float | None
    super_large_net: float | None
    large_net: float | None
    medium_net: float | None
    small_net: float | None


class FundFlowResponse(BaseModel):
    symbol: str
    rows: list[FundFlowRowDTO]


class ProfileResponse(BaseModel):
    symbol: str
    name: str | None
    market: str | None


class SearchHit(BaseModel):
    symbol: str
    name: str
    market: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(20, ge=1, le=50),
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> SearchResponse:
    hits = await svc.search(q, limit)
    return SearchResponse(query=q, hits=[
        SearchHit(symbol=s, name=n, market=m) for s, n, m in hits
    ])


@router.get("/{symbol}/profile", response_model=ProfileResponse)
async def profile(
    symbol: str,
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> ProfileResponse:
    name = await svc.get_name(symbol)
    # market 推断:简单从后缀拿
    market = None
    if symbol.endswith((".SH", ".SZ", ".BJ")):
        market = "ashare"
    elif symbol.endswith(".HK"):
        market = "hk"
    return ProfileResponse(symbol=symbol, name=name, market=market)


@router.get("/{symbol}/bars", response_model=BarsResponse)
async def bars(
    symbol: str,
    interval: str = Query("1d"),
    days: int = Query(365, ge=1, le=3650),
    svc: KLineService = Depends(get_kline_service),
) -> BarsResponse:
    if interval not in _VALID_INTERVALS:
        raise HTTPException(400, f"invalid interval: {interval}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = await svc.get_bars(symbol, interval=interval, start=start, end=end)
    return BarsResponse(
        symbol=symbol, interval=interval,
        bars=[BarDTO(
            ts=b.ts.isoformat(),
            open=float(b.open), high=float(b.high),
            low=float(b.low), close=float(b.close),
            volume=b.volume,
        ) for b in bars],
    )


@router.get("/{symbol}/fund_flow", response_model=FundFlowResponse)
async def fund_flow(
    symbol: str,
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> FundFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.query_symbol(symbol, start, end)
    return FundFlowResponse(
        symbol=symbol,
        rows=[FundFlowRowDTO(
            ts=r.ts.isoformat(), main_net=r.main_net,
            super_large_net=r.super_large_net, large_net=r.large_net,
            medium_net=r.medium_net, small_net=r.small_net,
        ) for r in rows],
    )
