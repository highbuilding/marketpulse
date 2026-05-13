from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import get_watchlist_service
from core.services.watchlist_service import WatchlistService

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


@router.post("/{wl_id}/symbols", status_code=204)
async def add_symbol(wl_id: int, body: AddSymbolBody,
                      svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.add_symbol(wl_id, body.symbol)


@router.delete("/{wl_id}/symbols/{symbol}", status_code=204)
async def remove_symbol(wl_id: int, symbol: str,
                         svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.remove_symbol(wl_id, symbol)
