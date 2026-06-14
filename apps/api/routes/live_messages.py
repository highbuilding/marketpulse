from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import get_live_message_repo, get_theme_repo, get_watchlist_service
from core.domain.models import LiveMessage
from core.persistence.live_message_repo import LiveMessageRepo
from core.persistence.theme_repo import ThemeRepo
from core.services.watchlist_service import WatchlistService


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


class LiveMessageStatusResp(BaseModel):
    market: str
    enabled_themes: int
    enabled_constituents: int
    watchlist_symbols: int
    latest_message_ts: str | None
    latest_message_title: str | None
    rule_version: str


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


@router.get("/status", response_model=LiveMessageStatusResp)
async def live_messages_status(
    market: str = Query("ashare"),
    repo: LiveMessageRepo = Depends(get_live_message_repo),
    theme_repo: ThemeRepo = Depends(get_theme_repo),
    watchlist: WatchlistService = Depends(get_watchlist_service),
) -> LiveMessageStatusResp:
    definitions = await theme_repo.list_definitions(market, include_disabled=False)
    constituent_count = 0
    for d in definitions:
        constituent_count += len(await theme_repo.list_static_constituents(
            market, d.theme_code, include_disabled=False))
    try:
        watch_symbols = [
            s for s in await watchlist.dynamic_universe()
            if market == "ashare" and (s.endswith(".SH") or s.endswith(".SZ"))
        ]
    except Exception:  # noqa: BLE001
        watch_symbols = []
    latest = await repo.list_recent(market, limit=1)
    latest_msg = latest[0] if latest else None
    return LiveMessageStatusResp(
        market=market,
        enabled_themes=len(definitions),
        enabled_constituents=constituent_count,
        watchlist_symbols=len(watch_symbols),
        latest_message_ts=latest_msg.ts.isoformat() if latest_msg else None,
        latest_message_title=latest_msg.title if latest_msg else None,
        rule_version=latest_msg.rule_version if latest_msg else "v2",
    )
