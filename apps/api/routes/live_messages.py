from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import get_live_message_repo
from core.domain.models import LiveMessage
from core.persistence.live_message_repo import LiveMessageRepo


router = APIRouter(prefix="/api/live-messages", tags=["live-messages"])


class LiveMessageDTO(BaseModel):
    id: str
    market: str
    ts: str
    level: str
    category: str
    title: str
    body: str
    theme_code: str | None
    symbol: str | None
    symbols: list[str]
    source_event: str
    source_event_id: str | None
    dedupe_key: str
    payload: dict[str, Any]
    rule_version: str
    created_at: str | None


class LiveMessagesResp(BaseModel):
    messages: list[LiveMessageDTO]


def _dto(m: LiveMessage) -> LiveMessageDTO:
    return LiveMessageDTO(
        id=m.id,
        market=m.market,
        ts=m.ts.isoformat(),
        level=m.level,
        category=m.category,
        title=m.title,
        body=m.body,
        theme_code=m.theme_code,
        symbol=m.symbol,
        symbols=m.symbols or [],
        source_event=m.source_event,
        source_event_id=m.source_event_id,
        dedupe_key=m.dedupe_key,
        payload=m.payload or {},
        rule_version=m.rule_version,
        created_at=m.created_at.isoformat() if m.created_at else None,
    )


@router.get("", response_model=LiveMessagesResp)
async def recent_live_messages(
    market: str = Query("ashare"),
    limit: int = Query(50, ge=1, le=200),
    category: str | None = Query(None),
    repo: LiveMessageRepo = Depends(get_live_message_repo),
) -> LiveMessagesResp:
    rows = await repo.list_recent(market, limit=limit, category=category)
    return LiveMessagesResp(messages=[_dto(m) for m in rows])


@router.get("/window", response_model=LiveMessagesResp)
async def live_messages_window(
    market: str = Query("ashare"),
    start: datetime = Query(...),
    end: datetime = Query(...),
    category: str | None = Query(None),
    limit: int = Query(500, ge=1, le=1000),
    repo: LiveMessageRepo = Depends(get_live_message_repo),
) -> LiveMessagesResp:
    rows = await repo.list_window(
        market,
        start=start.astimezone(timezone.utc),
        end=end.astimezone(timezone.utc),
        category=category,
        limit=limit,
    )
    return LiveMessagesResp(messages=[_dto(m) for m in rows])


@router.get("/ai-context", response_model=LiveMessagesResp)
async def live_messages_ai_context(
    market: str = Query("ashare"),
    minutes: int = Query(30, ge=5, le=240),
    limit: int = Query(100, ge=1, le=200),
    repo: LiveMessageRepo = Depends(get_live_message_repo),
) -> LiveMessagesResp:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    rows = await repo.list_window(market, start=start, end=end, limit=limit)
    return LiveMessagesResp(messages=[_dto(m) for m in rows])

