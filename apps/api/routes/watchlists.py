from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import get_redis_cache, get_watchlist_service
from core.domain.intervals import SIGNAL_INTERVALS
from core.domain.markets import infer_market
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


async def _refill_new_symbol(symbol: str, redis_cache, watchlist) -> None:
    """新加自选: 发 refill 让对应市场 collector 立即拉该标的历史 bar。
    api 进程无 DuckDB(雷区6), 不能自己拉/扫, 必须经 refill 交给 collector。
    仅 CORE 名单内标的可触发(前端不可触发名单外采集)。fire-and-forget。"""
    from core.domain.core_symbols import core_symbols
    from core.domain.markets import infer_market
    mkt = infer_market(symbol)
    if symbol not in core_symbols(mkt):
        log.info("watchlist.refill_skip_non_core", symbol=symbol, market=mkt)
        return
    from apps.api.routes.symbols import _publish_refill_request
    days_map = {"5m": 30, "15m": 30, "30m": 60, "60m": 120, "4h": 365, "1d": 1825}
    for iv in ("5m", *SIGNAL_INTERVALS):
        try:
            await _publish_refill_request(
                redis_cache, symbol, iv, days_map.get(iv, 60), watchlist=watchlist)
        except Exception as e:  # noqa: BLE001
            log.warning("watchlist.refill_failed",
                        symbol=symbol, interval=iv, error=str(e))


@router.post("/{wl_id}/symbols", status_code=204)
async def add_symbol(wl_id: int, body: AddSymbolBody,
                     bg: BackgroundTasks,
                     svc: WatchlistService = Depends(get_watchlist_service),
                     redis_cache=Depends(get_redis_cache)) -> None:
    from core.services.watchlist_service import SymbolNotCollectedError
    try:
        await svc.add_symbol(wl_id, body.symbol)
    except SymbolNotCollectedError as e:
        raise HTTPException(400, str(e)) from e
    # 后台发 refill, 让对应市场 collector 立即拉该标的历史 bar(api 无 DuckDB)。接口立即返回。
    bg.add_task(_refill_new_symbol, body.symbol, redis_cache, svc)


@router.delete("/{wl_id}/symbols/{symbol}", status_code=204)
async def remove_symbol(wl_id: int, symbol: str,
                         svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.remove_symbol(wl_id, symbol)
