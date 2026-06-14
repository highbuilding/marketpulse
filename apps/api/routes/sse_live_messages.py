from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse


router = APIRouter(prefix="/api/sse", tags=["sse"])

_PING_TIMEOUT_S = 25


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _stream_gen(hub, market: str):
    sub = hub.register([market])
    try:
        yield _sse_event("connected", {"server_ts": datetime.now(timezone.utc).isoformat()})
        while True:
            try:
                payload = await asyncio.wait_for(sub.get(), timeout=_PING_TIMEOUT_S)
            except asyncio.TimeoutError:
                yield _sse_event("ping", {"server_ts": datetime.now(timezone.utc).isoformat()})
                continue
            yield _sse_event("live-message", payload)
    finally:
        hub.unregister([market], sub)


@router.get("/live-messages")
async def sse_live_messages(request: Request, market: str = Query("ashare")):
    hub = request.app.state.live_message_hub
    return StreamingResponse(
        _stream_gen(hub, market),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

