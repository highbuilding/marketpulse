"""SSE: 分时图(时分线)增量 push。

GET /api/sse/intraday/{symbol}
事件: connected / init(当前点快照) / point(分时点更新) / ping(心跳)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])
PING_INTERVAL_S = 30


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _gen(symbol: str, hub, redis_cache):
    market = infer_market(symbol)
    sub = hub.register([symbol])
    try:
        yield _sse_event("connected", {"symbol": symbol,
                                       "server_ts": datetime.now(timezone.utc).isoformat()})
        # init: 推当前分时点快照
        try:
            cur = await redis_cache.get_msgpack(keys.cache_intraday_current(market, symbol))
        except Exception:  # noqa: BLE001
            cur = None
        if cur:
            yield _sse_event("init", {"point": cur, "symbol": symbol})

        while True:
            try:
                payload = await asyncio.wait_for(sub.get(), timeout=PING_INTERVAL_S)
            except asyncio.TimeoutError:
                yield _sse_event("ping", {"server_ts": datetime.now(timezone.utc).isoformat()})
                continue
            except asyncio.CancelledError:
                return
            yield _sse_event("point", payload)
    finally:
        hub.unregister([symbol], sub)


@router.get("/intraday/{symbol}")
async def sse_intraday(request: Request, symbol: str, redis_cache=Depends(get_redis_cache)):
    hub = request.app.state.intraday_hub
    return StreamingResponse(
        _gen(symbol, hub, redis_cache), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})
