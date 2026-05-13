from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import get_fund_flow_service
from core.services.fund_flow_service import FundFlowService

router = APIRouter(prefix="/api/north_flow", tags=["north_flow"])


class NorthFlowRow(BaseModel):
    ts: str
    hgt_net: float | None
    sgt_net: float | None


class NorthFlowResponse(BaseModel):
    rows: list[NorthFlowRow]


@router.get("", response_model=NorthFlowResponse)
async def north_flow(
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> NorthFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.query_north(start, end)
    return NorthFlowResponse(rows=[
        NorthFlowRow(ts=r.ts.isoformat(), hgt_net=r.hgt_net, sgt_net=r.sgt_net)
        for r in rows
    ])
