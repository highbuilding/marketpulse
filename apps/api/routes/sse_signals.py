"""SSE: 订阅 bus:signal.new 转发浏览器。全量推, 前端按市场过滤。

复用 StreamHub(单读多分发): main lifespan 起 app.state.signal_hub.run(),
本端点只 register/取队列/注销。新信号实时推给在线前端; 离线无所谓(fire-and-forget)。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/sse", tags=["sse"])

_SIGNAL_KEY = "*"  # 全量分发(前端按市场过滤)
_PING_TIMEOUT_S = 25


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _stream_gen(hub):
    sub = hub.register([_SIGNAL_KEY])
    try:
        yield _sse_event("connected", {"server_ts": datetime.now(timezone.utc).isoformat()})
        while True:
            try:
                payload = await asyncio.wait_for(sub.get(), timeout=_PING_TIMEOUT_S)
            except asyncio.TimeoutError:
                yield _sse_event("ping", {"server_ts": datetime.now(timezone.utc).isoformat()})
                continue
            yield _sse_event("signal", payload)
    finally:
        hub.unregister([_SIGNAL_KEY], sub)


@router.get("/signals")
async def sse_signals(request: Request):
    hub = request.app.state.signal_hub
    return StreamingResponse(
        _stream_gen(hub),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
