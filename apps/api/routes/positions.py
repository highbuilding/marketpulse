from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.deps import get_position_service
from core.domain.models import Position
from core.positions.service import PositionService


router = APIRouter(prefix="/api/positions", tags=["positions"])


class PositionDTO(BaseModel):
    id: int
    market: str
    symbol: str
    name: str | None
    quantity: int
    cost_price: float | None
    opened_at: str | None
    strategy_tag: str | None
    entry_reason: str | None
    status: str
    note: str | None
    created_at: str | None
    updated_at: str | None


class PositionsResp(BaseModel):
    positions: list[PositionDTO]


class UpsertPositionBody(BaseModel):
    market: str = "ashare"
    symbol: str = Field(..., min_length=1)
    name: str | None = None
    quantity: int = Field(default=0, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    opened_at: str | None = None
    strategy_tag: str | None = None
    entry_reason: str | None = None
    status: str = "active"
    note: str | None = None


class PositionIdResp(BaseModel):
    id: int


class PatchPositionBody(BaseModel):
    name: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    opened_at: str | None = None
    strategy_tag: str | None = None
    entry_reason: str | None = None
    status: str | None = None
    note: str | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "invalid opened_at datetime") from exc


def _dto(p: Position) -> PositionDTO:
    return PositionDTO(
        id=p.id or 0,
        market=p.market,
        symbol=p.symbol,
        name=p.name,
        quantity=p.quantity,
        cost_price=p.cost_price,
        opened_at=p.opened_at.isoformat() if p.opened_at else None,
        strategy_tag=p.strategy_tag,
        entry_reason=p.entry_reason,
        status=p.status,
        note=p.note,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


def _handle_unsupported(exc: ValueError) -> None:
    raise HTTPException(400, str(exc)) from exc


@router.get("", response_model=PositionsResp)
async def list_positions(
    market: str = Query("ashare"),
    include_closed: bool = Query(False),
    svc: PositionService = Depends(get_position_service),
) -> PositionsResp:
    try:
        rows = await svc.list_positions(market, include_closed=include_closed)
    except ValueError as exc:
        _handle_unsupported(exc)
    return PositionsResp(positions=[_dto(p) for p in rows])


@router.post("", response_model=PositionIdResp)
async def upsert_position(
    body: UpsertPositionBody,
    svc: PositionService = Depends(get_position_service),
) -> PositionIdResp:
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol cannot be empty")
    try:
        row_id = await svc.upsert_position(
            Position(
                market=body.market,
                symbol=symbol,
                name=body.name.strip() if body.name else None,
                quantity=body.quantity,
                cost_price=body.cost_price,
                opened_at=_parse_dt(body.opened_at),
                strategy_tag=body.strategy_tag,
                entry_reason=body.entry_reason,
                status=body.status,
                note=body.note,
            ),
        )
    except ValueError as exc:
        _handle_unsupported(exc)
    return PositionIdResp(id=row_id)


@router.patch("/{symbol}", response_model=PositionIdResp)
async def patch_position(
    symbol: str,
    body: PatchPositionBody,
    market: str = Query("ashare"),
    svc: PositionService = Depends(get_position_service),
) -> PositionIdResp:
    sym = symbol.strip().upper()
    try:
        existing = await svc.get_position(market, sym)
    except ValueError as exc:
        _handle_unsupported(exc)
    if existing is None:
        raise HTTPException(404, "position not found")
    row_id = await svc.upsert_position(
        Position(
            market=market,
            symbol=sym,
            name=body.name if body.name is not None else existing.name,
            quantity=body.quantity if body.quantity is not None else existing.quantity,
            cost_price=body.cost_price if body.cost_price is not None else existing.cost_price,
            opened_at=_parse_dt(body.opened_at) if body.opened_at is not None else existing.opened_at,
            strategy_tag=body.strategy_tag if body.strategy_tag is not None else existing.strategy_tag,
            entry_reason=body.entry_reason if body.entry_reason is not None else existing.entry_reason,
            status=body.status if body.status is not None else existing.status,
            note=body.note if body.note is not None else existing.note,
            created_at=existing.created_at,
        ),
    )
    return PositionIdResp(id=row_id)


@router.delete("/{symbol}", status_code=204)
async def close_position(
    symbol: str,
    market: str = Query("ashare"),
    svc: PositionService = Depends(get_position_service),
) -> None:
    try:
        await svc.close_position(market, symbol.strip().upper())
    except ValueError as exc:
        _handle_unsupported(exc)
