"""SSE: 分时图(时分线)增量 push。

GET /api/sse/intraday/{symbol}
事件: connected / init(当前点快照) / point(分时点更新) / ping(心跳)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])
PING_INTERVAL_S = 30


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _gen(symbol: str, redis_cache):
    market = infer_market(symbol)
    yield _sse_event("connected", {"symbol": symbol,
                                   "server_ts": datetime.now(timezone.utc).isoformat()})

    # init: 推当前分时点快照
    try:
        cur = await redis_cache.get_msgpack(keys.cache_intraday_current(market, symbol))
    except Exception:  # noqa: BLE001
        cur = None
    if cur:
        yield _sse_event("init", {"point": cur, "symbol": symbol})

    # xread 游标: 先用 $ 取当前 stream 位置 (非阻塞), 再进阻塞循环
    # 与 sse_bars 保持一致, 避免漏消息
    try:
        cursor_entries = await redis_cache._r.xread(  # noqa: SLF001
            streams={keys.BUS_INTRADAY_UPDATED: "$"},
            count=1, block=0,
        )
    except Exception:  # noqa: BLE001
        cursor_entries = None

    last_id = "$"
    if cursor_entries:
        for _stream, msgs in cursor_entries:
            if msgs:
                last_id = msgs[-1][0]

    last_ping = datetime.now(timezone.utc)
    while True:
        try:
            entries = await redis_cache._r.xread(  # noqa: SLF001
                streams={keys.BUS_INTRADAY_UPDATED: last_id},
                count=20, block=PING_INTERVAL_S * 1000,
            )
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.warning("sse_intraday.read_failed", error=str(e))
            await asyncio.sleep(1)
            continue

        now = datetime.now(timezone.utc)
        if entries:
            for _stream, msgs in entries:
                for msg_id, fields in msgs:
                    last_id = msg_id
                    try:
                        raw = fields.get(b"data") or fields.get("data")
                        if raw is None:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        payload = json.loads(raw)
                        if payload.get("symbol") == symbol:
                            yield _sse_event("point", payload)
                            last_ping = now
                    except asyncio.CancelledError:
                        return
                    except Exception as e:  # noqa: BLE001
                        log.warning("sse_intraday.parse_failed", error=str(e))

        if (now - last_ping).total_seconds() >= PING_INTERVAL_S:
            yield _sse_event("ping", {"server_ts": now.isoformat()})
            last_ping = now


@router.get("/intraday/{symbol}")
async def sse_intraday(symbol: str, redis_cache=Depends(get_redis_cache)):
    return StreamingResponse(
        _gen(symbol, redis_cache), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})
