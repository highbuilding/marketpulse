"""通知子系统 API: 收件人 + symbol 配置 + 测试发送。"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.deps import get_notification_repo, get_notification_service
from core.domain.intervals import SIGNAL_INTERVALS
from core.persistence.notification_repo import NotificationRepo
from core.services.notification_service import NotificationService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

VALID_MARKETS = {"ashare", "us", "hk", "crypto"}
VALID_CHANNELS = {"email", "wechat"}


class RecipientDTO(BaseModel):
    id: int
    market: str
    channel: str
    address: str
    enabled: bool


class RecipientsResp(BaseModel):
    recipients: list[RecipientDTO]


class AddRecipientBody(BaseModel):
    market: str
    channel: str = "email"
    address: str = Field(..., min_length=1)


class AddRecipientResp(BaseModel):
    id: int


class SetEnabledBody(BaseModel):
    enabled: bool


class SymbolConfigDTO(BaseModel):
    symbol: str
    intervals: list[str]


class SymbolConfigsResp(BaseModel):
    configs: list[SymbolConfigDTO]


class UpsertConfigBody(BaseModel):
    intervals: list[str]


class TestSendResp(BaseModel):
    ok: bool
    sent_to: int
    error: str | None = None


# ---------- recipients ----------

@router.get("/recipients", response_model=RecipientsResp)
async def list_recipients(
    market: str | None = Query(None),
    repo: NotificationRepo = Depends(get_notification_repo),
) -> RecipientsResp:
    if market is not None and market not in VALID_MARKETS:
        raise HTTPException(400, f"invalid market; must be one of {sorted(VALID_MARKETS)}")
    rs = await repo.list_recipients(market=market)
    return RecipientsResp(recipients=[
        RecipientDTO(id=r.id, market=r.market, channel=r.channel,
                     address=r.address, enabled=r.enabled)
        for r in rs
    ])


@router.post("/recipients", response_model=AddRecipientResp)
async def add_recipient(
    body: AddRecipientBody,
    repo: NotificationRepo = Depends(get_notification_repo),
) -> AddRecipientResp:
    if body.market not in VALID_MARKETS:
        raise HTTPException(400, f"invalid market: {body.market}")
    if body.channel not in VALID_CHANNELS:
        raise HTTPException(400, f"invalid channel: {body.channel}")
    address = body.address.strip()
    if not address:
        raise HTTPException(400, "address cannot be empty")
    if body.channel == "email" and "@" not in address:
        raise HTTPException(400, "invalid email address")
    try:
        new_id = await repo.add_recipient(body.market, body.channel, address)
    except Exception as e:  # noqa: BLE001 - aiosqlite UNIQUE 冲突
        if "UNIQUE" in str(e):
            raise HTTPException(409, "recipient already exists") from e
        raise
    return AddRecipientResp(id=new_id)


@router.patch("/recipients/{rid}", status_code=204)
async def set_enabled(
    rid: int, body: SetEnabledBody,
    repo: NotificationRepo = Depends(get_notification_repo),
) -> None:
    await repo.set_enabled(rid, body.enabled)


@router.delete("/recipients/{rid}", status_code=204)
async def delete_recipient(
    rid: int,
    repo: NotificationRepo = Depends(get_notification_repo),
) -> None:
    await repo.delete_recipient(rid)


# ---------- symbol config ----------

@router.get("/symbol-config", response_model=SymbolConfigsResp)
async def list_configs(
    repo: NotificationRepo = Depends(get_notification_repo),
) -> SymbolConfigsResp:
    cfgs = await repo.list_symbol_configs()
    return SymbolConfigsResp(configs=[
        SymbolConfigDTO(symbol=c.symbol, intervals=c.intervals) for c in cfgs
    ])


@router.put("/symbol-config/{symbol}", status_code=204)
async def upsert_config(
    symbol: str, body: UpsertConfigBody,
    repo: NotificationRepo = Depends(get_notification_repo),
) -> None:
    invalid = [iv for iv in body.intervals if iv not in SIGNAL_INTERVALS]
    if invalid:
        raise HTTPException(400, f"invalid intervals: {invalid}; allowed: {SIGNAL_INTERVALS}")
    await repo.upsert_symbol_config(symbol, body.intervals)


@router.delete("/symbol-config/{symbol}", status_code=204)
async def delete_config(
    symbol: str,
    repo: NotificationRepo = Depends(get_notification_repo),
) -> None:
    await repo.delete_symbol_config(symbol)


# ---------- test send ----------

@router.post("/test", response_model=TestSendResp)
async def send_test(
    market: str = Query(...),
    svc: NotificationService = Depends(get_notification_service),
) -> TestSendResp:
    if market not in VALID_MARKETS:
        raise HTTPException(400, f"invalid market: {market}")
    ok, count, err = await svc.send_test(market)
    return TestSendResp(ok=ok, sent_to=count, error=err)
