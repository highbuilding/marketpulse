from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.deps import get_registry
from core.adapters.registry import AdapterRegistry
from core.domain.models import HealthStatus

router = APIRouter(prefix="/api", tags=["health"])


class AdapterHealth(BaseModel):
    state: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    markets_enabled: list[str]
    adapters: dict[str, AdapterHealth]


def _overall(statuses: list[HealthStatus]) -> str:
    states = {s.state for s in statuses}
    if states <= {"ok"}:
        return "ok"
    if "down" in states:
        return "down"
    return "degraded"


@router.get("/health", response_model=HealthResponse)
async def health(registry: AdapterRegistry = Depends(get_registry)) -> HealthResponse:
    statuses: list[HealthStatus] = []
    for market in registry.markets():
        statuses.append(await registry.get(market).health())
    return HealthResponse(
        status=_overall(statuses),
        markets_enabled=registry.markets(),
        adapters={s.name: AdapterHealth(state=s.state, detail=s.detail) for s in statuses},
    )
