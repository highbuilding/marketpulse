from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import (
    get_fund_flow_service, get_kline_service, get_quote_cache, get_symbol_directory_service,
)
from core.cache.quote_cache import QuoteCache
from core.domain.intervals import KLINE_INTERVALS
from core.domain.markets import infer_market
from core.services.fund_flow_service import FundFlowService
from core.services.kline_service import KLineService
from core.services.symbol_directory_service import SymbolDirectoryService

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


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


class ProfilesResponse(BaseModel):
    profiles: list[ProfileResponse]


class QuoteResponse(BaseModel):
    symbol: str
    price: float | None
    change_pct: float | None
    volume: int | None
    ts: str | None


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




@router.get("/profiles", response_model=ProfilesResponse)
async def profiles(
    symbols: str = Query(..., description="逗号分隔的 symbol 列表"),
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> ProfilesResponse:
    """批量取 profile, 给前端"信号流 / 关注页"消除 N+1 用。
    缺失的 symbol 也返回(name=None), 让前端能整齐渲染。"""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        return ProfilesResponse(profiles=[])
    names = await svc.get_names(syms)
    return ProfilesResponse(profiles=[
        ProfileResponse(symbol=s, name=names.get(s), market=infer_market(s))
        for s in syms
    ])


@router.get("/{symbol}/profile", response_model=ProfileResponse)
async def profile(
    symbol: str,
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> ProfileResponse:
    name = await svc.get_name(symbol)
    return ProfileResponse(symbol=symbol, name=name, market=infer_market(symbol))


@router.get("/{symbol}/quote", response_model=QuoteResponse)
async def quote(
    symbol: str,
    cache: QuoteCache = Depends(get_quote_cache),
) -> QuoteResponse:
    market = infer_market(symbol)
    q = cache.get(market, symbol) if market else None
    if q is None:
        return QuoteResponse(symbol=symbol, price=None, change_pct=None, volume=None, ts=None)
    return QuoteResponse(
        symbol=symbol, price=float(q.price), change_pct=q.change_pct,
        volume=q.volume, ts=q.ts.isoformat(),
    )


@router.get("/{symbol}/bars", response_model=BarsResponse)
async def bars(
    symbol: str,
    interval: str = Query("1d"),
    days: int = Query(365, ge=1, le=3650),
    svc: KLineService = Depends(get_kline_service),
) -> BarsResponse:
    if interval not in KLINE_INTERVALS:
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
