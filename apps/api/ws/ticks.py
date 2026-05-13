from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from apps.api.deps import get_quote_cache

log = structlog.get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/ticks")
async def ticks(ws: WebSocket):
    await ws.accept()
    cache = get_quote_cache()
    try:
        while True:
            markets = ("ashare", "hk", "us", "crypto")
            payload = []
            for m in markets:
                for q in cache.snapshot(m):
                    payload.append({
                        "market": q.market, "symbol": q.symbol,
                        "price": float(q.price), "change_pct": q.change_pct,
                        "ts": q.ts.isoformat(),
                    })
            await ws.send_text(json.dumps({"type": "ticks", "data": payload}))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        log.debug("ws.ticks_disconnected")
