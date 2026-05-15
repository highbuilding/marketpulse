from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import get_signal_scan_service, get_watchlist_service
from core.domain.intervals import SIGNAL_INTERVALS
from core.services.signal_service import SignalScanService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


class WatchlistDTO(BaseModel):
    id: int
    name: str
    is_archived: bool
    created_at: str


class ListResponse(BaseModel):
    watchlists: list[WatchlistDTO]


class CreateBody(BaseModel):
    name: str


class CreateResp(BaseModel):
    id: int


class RenameBody(BaseModel):
    name: str


class SymbolsResp(BaseModel):
    watchlist_id: int
    symbols: list[str]


class AddSymbolBody(BaseModel):
    symbol: str


@router.get("", response_model=ListResponse)
async def list_all(svc: WatchlistService = Depends(get_watchlist_service)) -> ListResponse:
    items = await svc.list_all()
    return ListResponse(watchlists=[
        WatchlistDTO(id=w.id, name=w.name, is_archived=w.is_archived,
                     created_at=w.created_at.isoformat())
        for w in items
    ])


@router.post("", response_model=CreateResp)
async def create(body: CreateBody, svc: WatchlistService = Depends(get_watchlist_service)) -> CreateResp:
    if not body.name.strip():
        raise HTTPException(400, "name cannot be empty")
    wl_id = await svc.create(body.name.strip())
    return CreateResp(id=wl_id)


@router.patch("/{wl_id}", status_code=204)
async def rename(wl_id: int, body: RenameBody,
                  svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    if not body.name.strip():
        raise HTTPException(400, "name cannot be empty")
    await svc.rename(wl_id, body.name.strip())


@router.delete("/{wl_id}", status_code=204)
async def archive(wl_id: int, svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.archive(wl_id)


@router.get("/{wl_id}/symbols", response_model=SymbolsResp)
async def list_symbols(wl_id: int,
                        svc: WatchlistService = Depends(get_watchlist_service)) -> SymbolsResp:
    syms = await svc.list_symbols(wl_id)
    return SymbolsResp(watchlist_id=wl_id, symbols=syms)


async def _initial_scan(symbol: str, scan: SignalScanService) -> None:
    """新加的 symbol 立刻在所有支持的周期上扫一次, 避免关注页要等到下一个 cron tick 才出信号。
    每个周期独立 try/except, 单个失败不影响其他。"""
    for iv in SIGNAL_INTERVALS:
        try:
            n = await scan.scan_symbol(symbol, iv)
            log.info("watchlist.initial_scan", symbol=symbol, interval=iv, new=n)
        except Exception as e:  # noqa: BLE001
            log.warning("watchlist.initial_scan_failed",
                        symbol=symbol, interval=iv, error=str(e))


@router.post("/{wl_id}/symbols", status_code=204)
async def add_symbol(wl_id: int, body: AddSymbolBody,
                      bg: BackgroundTasks,
                      svc: WatchlistService = Depends(get_watchlist_service),
                      scan: SignalScanService = Depends(get_signal_scan_service)) -> None:
    await svc.add_symbol(wl_id, body.symbol)
    # 后台异步扫一次, 接口立即返回; 失败不影响 add 成功
    bg.add_task(_initial_scan, body.symbol, scan)


@router.delete("/{wl_id}/symbols/{symbol}", status_code=204)
async def remove_symbol(wl_id: int, symbol: str,
                         svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.remove_symbol(wl_id, symbol)
