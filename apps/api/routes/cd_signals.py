"""CD 信号查询/管理端点。

GET  /api/cd-signals                          列出最近信号(可按 interval/symbol/ack 过滤)
GET  /api/cd-signals/by-symbol/{symbol}       某只标的的全部信号(K 图 markers 用)
GET  /api/cd-signals/watchlist-events         关注列表所有标的某周期信号倒序(关注页"信号事件流"用)
POST /api/cd-signals/{id}/ack                 标记已读
POST /api/cd-signals/scan                     手动触发一次扫描(对账/补算用)
GET  /api/cd-signals/unack-count              顶栏未读 badge
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import (
    get_signal_repo, get_signal_scan_service, get_watchlist_service,
)
from core.domain.intervals import SIGNAL_INTERVALS, SIGNAL_INTERVALS_SET
from core.persistence.signal_repo import SignalRepo
from core.services.signal_service import SignalScanService
from core.services.watchlist_service import WatchlistService

router = APIRouter(prefix="/api/cd-signals", tags=["cd-signals"])


def _is_crypto(symbol: str) -> bool:
    """股票后缀 .SH/.SZ/.BJ/.HK 之外的视为 crypto。
    美股 ticker 无后缀(AAPL 等), 这里也算非 crypto。"""
    return not symbol.endswith((".SH", ".SZ", ".BJ", ".HK")) and "/" in symbol


SignalIntervalT = Literal["15m", "30m", "60m", "4h", "1d"]


class SignalDTO(BaseModel):
    id: int
    symbol: str
    interval: SignalIntervalT
    signal_type: Literal["buy", "sell"]
    bar_ts: str
    detected_at: str
    price: float
    d_value: float | None
    acknowledged: bool


class ListResponse(BaseModel):
    signals: list[SignalDTO]


class ScanBody(BaseModel):
    symbols: list[str] | None = None  # None -> 关注列表中所有标的
    intervals: list[str] | None = None  # None -> SIGNAL_INTERVALS (15m/30m/60m/4h/1d)


class ScanResponse(BaseModel):
    new_signals: int
    interval_breakdown: dict[str, int]


class UnackCountResponse(BaseModel):
    count: int


def _to_dto(s) -> SignalDTO:
    return SignalDTO(
        id=s.id, symbol=s.symbol, interval=s.interval,
        signal_type=s.signal_type,
        bar_ts=s.bar_ts.isoformat(),
        detected_at=s.detected_at.isoformat(),
        price=s.price, d_value=s.d_value,
        acknowledged=s.acknowledged,
    )


@router.get("", response_model=ListResponse)
async def list_signals(
    since: datetime | None = None,
    intervals: list[str] | None = Query(None),
    symbol: str | None = None,
    only_unack: bool = False,
    limit: int = 200,
    repo: SignalRepo = Depends(get_signal_repo),
) -> ListResponse:
    if intervals:
        for iv in intervals:
            if iv not in SIGNAL_INTERVALS_SET:
                raise HTTPException(400, f"unsupported interval: {iv}")
    sigs = await repo.list_recent(
        since=since, intervals=intervals,
        symbols=[symbol] if symbol else None,
        only_unacknowledged=only_unack, limit=limit,
    )
    return ListResponse(signals=[_to_dto(s) for s in sigs])


@router.get("/by-symbol/{symbol}", response_model=ListResponse)
async def by_symbol(
    symbol: str,
    intervals: list[str] | None = Query(None),
    limit: int = 500,
    repo: SignalRepo = Depends(get_signal_repo),
) -> ListResponse:
    if intervals:
        for iv in intervals:
            if iv not in SIGNAL_INTERVALS_SET:
                raise HTTPException(400, f"unsupported interval: {iv}")
    sigs = await repo.list_by_symbol(symbol, intervals=intervals, limit=limit)
    return ListResponse(signals=[_to_dto(s) for s in sigs])


@router.get("/watchlist-events", response_model=ListResponse)
async def watchlist_events(
    interval: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    repo: SignalRepo = Depends(get_signal_repo),
    wl_svc: WatchlistService = Depends(get_watchlist_service),
) -> ListResponse:
    """关注列表的所有标的在指定周期上的最近 N 条信号(按 bar_ts 倒序)。
    4h 仅 crypto 标的有意义(股票 4h≡1d), 自动按市场过滤。"""
    if interval not in SIGNAL_INTERVALS_SET:
        raise HTTPException(400, f"unsupported interval: {interval}")
    symbols = await wl_svc.dynamic_universe()
    if interval == "4h":
        symbols = [s for s in symbols if _is_crypto(s)]
    if not symbols:
        return ListResponse(signals=[])
    sigs = await repo.list_recent(intervals=[interval], symbols=symbols, limit=limit)
    return ListResponse(signals=[_to_dto(s) for s in sigs])


@router.post("/{signal_id}/ack", status_code=204)
async def ack_signal(signal_id: int, repo: SignalRepo = Depends(get_signal_repo)) -> None:
    await repo.acknowledge(signal_id)


@router.post("/scan", response_model=ScanResponse)
async def scan(
    body: ScanBody,
    scan_svc: SignalScanService = Depends(get_signal_scan_service),
    wl_svc: WatchlistService = Depends(get_watchlist_service),
) -> ScanResponse:
    intervals = body.intervals or list(SIGNAL_INTERVALS)
    for iv in intervals:
        if iv not in SIGNAL_INTERVALS_SET:
            raise HTTPException(400, f"unsupported interval: {iv}")
    symbols = body.symbols if body.symbols else await wl_svc.dynamic_universe()
    if not symbols:
        return ScanResponse(new_signals=0, interval_breakdown={})
    breakdown: dict[str, int] = {}
    for iv in intervals:
        n = await scan_svc.scan_many(symbols, iv)
        breakdown[iv] = n
    return ScanResponse(new_signals=sum(breakdown.values()), interval_breakdown=breakdown)


@router.get("/unack-count", response_model=UnackCountResponse)
async def unack_count(repo: SignalRepo = Depends(get_signal_repo)) -> UnackCountResponse:
    return UnackCountResponse(count=await repo.count_unacknowledged())
