from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_fund_flow_service, get_sector_service
from core.services.fund_flow_service import FundFlowService
from core.services.sector_service import SectorService

router = APIRouter(prefix="/api/sectors", tags=["sectors-detail"])


class SectorInfo(BaseModel):
    name: str
    classification: str
    updated_at: str


class SectorListResponse(BaseModel):
    sectors: list[SectorInfo]


class ConstituentsResponse(BaseModel):
    sector_name: str
    symbols: list[str]


class SectorFundFlowRow(BaseModel):
    ts: str
    main_net: float | None
    pct_change: float | None


class SectorFundFlowResponse(BaseModel):
    sector_name: str
    rows: list[SectorFundFlowRow]


@router.get("/list", response_model=SectorListResponse)
async def sector_list(svc: SectorService = Depends(get_sector_service)) -> SectorListResponse:
    sectors = await svc.list_sectors()
    return SectorListResponse(sectors=[
        SectorInfo(name=s.name, classification=s.classification,
                   updated_at=s.updated_at.isoformat())
        for s in sectors
    ])


@router.get("/{name}/constituents", response_model=ConstituentsResponse)
async def sector_constituents(
    name: str,
    svc: SectorService = Depends(get_sector_service),
) -> ConstituentsResponse:
    syms = await svc.list_constituents(name)
    if not syms:
        raise HTTPException(404, f"sector not found or empty: {name}")
    return ConstituentsResponse(sector_name=name, symbols=syms)


@router.get("/{name}/fund_flow", response_model=SectorFundFlowResponse)
async def sector_fund_flow(
    name: str,
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> SectorFundFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.repo.query_sector_flow(name, start, end)
    return SectorFundFlowResponse(
        sector_name=name,
        rows=[SectorFundFlowRow(ts=r.ts.isoformat(),
                                 main_net=r.main_net, pct_change=r.pct_change)
              for r in rows],
    )
