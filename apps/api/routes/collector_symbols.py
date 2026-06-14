from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.deps import (
    get_collector_symbol_repo,
    get_redis_cache,
    get_symbol_directory_service,
)
from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.markets import infer_market
from core.domain.models import CollectorSymbol
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.services.symbol_directory_service import SymbolDirectoryService


router = APIRouter(prefix="/api/collector-symbols", tags=["collector-symbols"])


class CollectorSymbolDTO(BaseModel):
    market: str
    symbol: str
    name: str | None
    enabled: bool
    source: str
    collect_snapshot: bool
    collect_5m: bool
    collect_signals: bool
    note: str | None
    created_at: str | None
    updated_at: str | None


class CollectorSymbolsResp(BaseModel):
    symbols: list[CollectorSymbolDTO]
    total: int
    enabled: int
    snapshot_count: int
    kline_5m_count: int
    signals_count: int


class CollectorSymbolBody(BaseModel):
    market: str = "ashare"
    symbol: str = Field(..., min_length=1)
    name: str | None = None
    enabled: bool = True
    collect_snapshot: bool = True
    collect_5m: bool = True
    collect_signals: bool = True
    note: str | None = None


class CollectorSymbolPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    collect_snapshot: bool | None = None
    collect_5m: bool | None = None
    collect_signals: bool | None = None
    note: str | None = None


class CollectorSymbolMutationResp(BaseModel):
    symbol: str
    action: str
    collector_confirmed: bool
    collector_message: str | None = None
    refill_queued: bool = False


def _dto(row: CollectorSymbol) -> CollectorSymbolDTO:
    return CollectorSymbolDTO(
        market=row.market,
        symbol=row.symbol,
        name=row.name,
        enabled=row.enabled,
        source=row.source,
        collect_snapshot=row.collect_snapshot,
        collect_5m=row.collect_5m,
        collect_signals=row.collect_signals,
        note=row.note,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _ensure_ashare(market: str) -> None:
    if market != "ashare":
        raise HTTPException(400, "collector symbol management currently supports ashare only")


async def _symbol_name(directory: SymbolDirectoryService, symbol: str) -> str | None:
    try:
        return await directory.get_name(symbol)
    except Exception:  # noqa: BLE001
        return None


async def _notify_collector(
    redis_cache: RedisCache,
    *,
    market: str,
    symbol: str,
    action: str,
    timeout_s: float = 3.0,
) -> tuple[bool, str | None]:
    request_id = uuid.uuid4().hex
    payload = {
        "request_id": request_id,
        "market": market,
        "symbol": symbol,
        "action": action,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    ack_key = keys.state_collector_symbol_ack(market, request_id)
    try:
        await redis_cache._r.xadd(  # noqa: SLF001
            keys.BUS_COLLECTOR_SYMBOLS_CHANGED,
            {"data": json.dumps(payload, ensure_ascii=False).encode()},
            maxlen=1000,
            approximate=True,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"collector event publish failed: {e}"
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        try:
            raw = await redis_cache.get_msgpack(ack_key)
        except Exception:  # noqa: BLE001
            raw = None
        if isinstance(raw, dict):
            ok = bool(raw.get("ok"))
            return ok, str(raw.get("message") or ("collector confirmed" if ok else "collector rejected"))
        await asyncio.sleep(0.15)
    return False, "collector confirm timeout"


async def _queue_refill(redis_cache: RedisCache, *, market: str, symbol: str) -> bool:
    payload = {"market": market, "symbol": symbol, "interval": "5m", "days": 60}
    try:
        await redis_cache._r.xadd(  # noqa: SLF001
            keys.BUS_BARS_REFILL_REQUEST,
            {"data": json.dumps(payload, ensure_ascii=False).encode()},
            maxlen=100,
            approximate=True,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


@router.get("", response_model=CollectorSymbolsResp)
async def list_collector_symbols(
    market: str = Query("ashare"),
    include_disabled: bool = Query(True),
    repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
) -> CollectorSymbolsResp:
    _ensure_ashare(market)
    rows = await repo.list(market, include_disabled=include_disabled)
    enabled = [r for r in rows if r.enabled]
    return CollectorSymbolsResp(
        symbols=[_dto(r) for r in rows],
        total=len(rows),
        enabled=len(enabled),
        snapshot_count=sum(1 for r in enabled if r.collect_snapshot),
        kline_5m_count=sum(1 for r in enabled if r.collect_5m),
        signals_count=sum(1 for r in enabled if r.collect_signals),
    )


@router.post("", response_model=CollectorSymbolMutationResp)
async def upsert_collector_symbol(
    body: CollectorSymbolBody,
    repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
    redis_cache: RedisCache = Depends(get_redis_cache),
    directory: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> CollectorSymbolMutationResp:
    _ensure_ashare(body.market)
    symbol = body.symbol.strip().upper()
    if infer_market(symbol) != "ashare":
        raise HTTPException(400, f"{symbol} is not an A-share symbol")
    existing = await repo.get(body.market, symbol)
    name = body.name.strip() if body.name else await _symbol_name(directory, symbol)
    await repo.upsert(CollectorSymbol(
        market=body.market,
        symbol=symbol,
        name=name,
        enabled=body.enabled,
        source=existing.source if existing and existing.source in {"core", "seed"} else "manual",
        collect_snapshot=body.collect_snapshot,
        collect_5m=body.collect_5m,
        collect_signals=body.collect_signals,
        note=body.note.strip() if body.note else None,
        created_at=existing.created_at if existing else None,
    ))
    confirmed, msg = await _notify_collector(redis_cache, market=body.market, symbol=symbol, action="upsert")
    refill = False
    if confirmed and body.enabled and body.collect_5m:
        refill = await _queue_refill(redis_cache, market=body.market, symbol=symbol)
    return CollectorSymbolMutationResp(
        symbol=symbol,
        action="upsert",
        collector_confirmed=confirmed,
        collector_message=msg,
        refill_queued=refill,
    )


@router.patch("/{symbol}", response_model=CollectorSymbolMutationResp)
async def patch_collector_symbol(
    symbol: str,
    body: CollectorSymbolPatch,
    market: str = Query("ashare"),
    repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
    redis_cache: RedisCache = Depends(get_redis_cache),
) -> CollectorSymbolMutationResp:
    _ensure_ashare(market)
    sym = symbol.strip().upper()
    existing = await repo.get(market, sym)
    if existing is None:
        raise HTTPException(404, "collector symbol not found")
    await repo.upsert(CollectorSymbol(
        market=market,
        symbol=sym,
        name=body.name if body.name is not None else existing.name,
        enabled=body.enabled if body.enabled is not None else existing.enabled,
        source=existing.source,
        collect_snapshot=body.collect_snapshot if body.collect_snapshot is not None else existing.collect_snapshot,
        collect_5m=body.collect_5m if body.collect_5m is not None else existing.collect_5m,
        collect_signals=body.collect_signals if body.collect_signals is not None else existing.collect_signals,
        note=body.note if body.note is not None else existing.note,
        created_at=existing.created_at,
    ))
    confirmed, msg = await _notify_collector(redis_cache, market=market, symbol=sym, action="patch")
    return CollectorSymbolMutationResp(
        symbol=sym,
        action="patch",
        collector_confirmed=confirmed,
        collector_message=msg,
    )


@router.delete("/{symbol}", response_model=CollectorSymbolMutationResp)
async def delete_collector_symbol(
    symbol: str,
    market: str = Query("ashare"),
    repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
    redis_cache: RedisCache = Depends(get_redis_cache),
) -> CollectorSymbolMutationResp:
    _ensure_ashare(market)
    sym = symbol.strip().upper()
    await repo.remove(market, sym)
    confirmed, msg = await _notify_collector(redis_cache, market=market, symbol=sym, action="delete")
    return CollectorSymbolMutationResp(
        symbol=sym,
        action="delete",
        collector_confirmed=confirmed,
        collector_message=msg,
    )
