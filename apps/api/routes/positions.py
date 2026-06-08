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
    close_price: float | None
    profit_amount: float | None
    profit_pct: float | None
    opened_at: str | None
    closed_at: str | None
    strategy_tag: str | None
    entry_reason: str | None
    status: str
    note: str | None
    created_at: str | None
    updated_at: str | None


class PositionsResp(BaseModel):
    positions: list[PositionDTO]


class CreatePositionBody(BaseModel):
    market: str = "ashare"
    symbol: str = Field(..., min_length=1)
    name: str | None = None
    quantity: int = Field(default=0, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    opened_at: str | None = None
    strategy_tag: str | None = None
    entry_reason: str | None = None
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
    note: str | None = None


class ClosePositionBody(BaseModel):
    """平仓: 手填平仓价, 盈亏由 (平仓价-开仓价)*股数 算出并存库。"""
    close_price: float | None = Field(default=None, ge=0)
    closed_at: str | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "invalid datetime") from exc


def _dto(p: Position) -> PositionDTO:
    return PositionDTO(
        id=p.id or 0,
        market=p.market,
        symbol=p.symbol,
        name=p.name,
        quantity=p.quantity,
        cost_price=p.cost_price,
        close_price=p.close_price,
        profit_amount=p.profit_amount,
        profit_pct=p.profit_pct,
        opened_at=p.opened_at.isoformat() if p.opened_at else None,
        closed_at=p.closed_at.isoformat() if p.closed_at else None,
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
async def create_position(
    body: CreatePositionBody,
    svc: PositionService = Depends(get_position_service),
) -> PositionIdResp:
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol cannot be empty")
    try:
        row_id = await svc.create_position(
            Position(
                market=body.market,
                symbol=symbol,
                name=body.name.strip() if body.name else None,
                quantity=body.quantity,
                cost_price=body.cost_price,
                opened_at=_parse_dt(body.opened_at),
                strategy_tag=body.strategy_tag,
                entry_reason=body.entry_reason,
                status="active",
                note=body.note,
            ),
        )
    except ValueError as exc:
        _handle_unsupported(exc)
    return PositionIdResp(id=row_id)


@router.patch("/{position_id}", response_model=PositionIdResp)
async def patch_position(
    position_id: int,
    body: PatchPositionBody,
    svc: PositionService = Depends(get_position_service),
) -> PositionIdResp:
    existing = await svc.get_position(position_id)
    if existing is None:
        raise HTTPException(404, "position not found")
    try:
        await svc.update_position(
            Position(
                id=position_id,
                market=existing.market,
                symbol=existing.symbol,
                name=body.name if body.name is not None else existing.name,
                quantity=body.quantity if body.quantity is not None else existing.quantity,
                cost_price=body.cost_price if body.cost_price is not None else existing.cost_price,
                close_price=existing.close_price,
                profit_amount=existing.profit_amount,
                profit_pct=existing.profit_pct,
                opened_at=_parse_dt(body.opened_at) if body.opened_at is not None else existing.opened_at,
                closed_at=existing.closed_at,
                strategy_tag=body.strategy_tag if body.strategy_tag is not None else existing.strategy_tag,
                entry_reason=body.entry_reason if body.entry_reason is not None else existing.entry_reason,
                status=existing.status,
                note=body.note if body.note is not None else existing.note,
                created_at=existing.created_at,
            ),
        )
    except ValueError as exc:
        _handle_unsupported(exc)
    return PositionIdResp(id=position_id)


@router.post("/{position_id}/close", response_model=PositionIdResp)
async def close_position(
    position_id: int,
    body: ClosePositionBody,
    svc: PositionService = Depends(get_position_service),
) -> PositionIdResp:
    """平仓: 手填平仓价 → 盈亏=(平仓价-开仓价)*股数 算出并存库。"""
    existing = await svc.get_position(position_id)
    if existing is None:
        raise HTTPException(404, "position not found")
    profit_amount: float | None = None
    profit_pct: float | None = None
    if body.close_price is not None and existing.cost_price:
        profit_amount = (body.close_price - existing.cost_price) * existing.quantity
        profit_pct = (body.close_price - existing.cost_price) / existing.cost_price * 100
    await svc.close_position(
        position_id, close_price=body.close_price,
        profit_amount=profit_amount, profit_pct=profit_pct,
    )
    return PositionIdResp(id=position_id)


@router.delete("/{position_id}", status_code=204)
async def delete_position(
    position_id: int,
    svc: PositionService = Depends(get_position_service),
) -> None:
    await svc.delete_position(position_id)
