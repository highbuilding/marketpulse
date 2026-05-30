"""SSE: K 线增量 push.

GET /api/sse/bars/{symbol}/{interval}         单标的
GET /api/sse/bars/batch?symbols=A,B,C&interval=5m  多标的 (批量, 1 连接)

事件:
- init  (当前进行中 bar 快照, 含 symbol 字段)
- bar   (k.x=true, 收盘 bar)
- tick  (k.x=false, 进行中 bar)
- ping  (心跳 30s)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])

PING_INTERVAL_S = 30


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _stream_gen(symbols: set[str], interval: str, redis_cache) -> None:
    """共享 generator: 订阅 bus, 匹配任意 symbol.

    用 xread (非 consumer group) 实现 pub-sub 语义:
    每条消息推给所有活跃 SSE 连接, 互不争抢.
    """
    server_ts = datetime.now(timezone.utc).isoformat()
    # 立刻发出 connected 事件, 防止浏览器在 init 为空时超时
    yield _sse_event("connected", {"symbols": list(symbols), "interval": interval, "server_ts": server_ts})

    for sym in symbols:
        market = infer_market(sym)
        try:
            current = await redis_cache.get_msgpack(
                keys.cache_bars_current(market, sym, interval),
            )
        except Exception:
            current = None
        if current:
            yield _sse_event("init", {"bars": [current], "symbol": sym, "server_ts": server_ts})

    # xread 游标: 先用 $ 取当前 stream 位置 (非阻塞), 再进阻塞循环
    try:
        cursor_entries = await redis_cache._r.xread(
            streams={keys.BUS_BARS_UPDATED: "$"},
            count=1, block=0,
        )
    except Exception:
        cursor_entries = None

    last_id = "$"
    if cursor_entries:
        for _stream, msgs in cursor_entries:
            if msgs:
                last_id = msgs[-1][0]  # 最后一条消息的 id

    last_ping = datetime.now(timezone.utc)
    while True:
        try:
            entries = await redis_cache._r.xread(
                streams={keys.BUS_BARS_UPDATED: last_id},
                count=20, block=PING_INTERVAL_S * 1000,
            )
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("sse.read_failed", error=str(e))
            await asyncio.sleep(1)
            continue

        now = datetime.now(timezone.utc)
        if entries:
            for _stream, msgs in entries:
                for msg_id, fields in msgs:
                    last_id = msg_id  # 推进游标
                    try:
                        data_raw = fields.get(b"data") or fields.get("data")
                        if data_raw is None:
                            continue
                        if isinstance(data_raw, bytes):
                            data_raw = data_raw.decode("utf-8", errors="replace")
                        payload = json.loads(data_raw)
                        if (payload.get("symbol") in symbols
                                and payload.get("interval") == interval):
                            event = "bar" if payload.get("final") else "tick"
                            yield _sse_event(event, payload)
                            last_ping = now
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        log.warning("sse.msg_parse_failed", error=str(e))

        if (now - last_ping).total_seconds() >= PING_INTERVAL_S:
            yield _sse_event("ping", {"server_ts": now.isoformat()})
            last_ping = now


@router.get("/bars/batch")
async def sse_bars_batch(
    symbols: str = Query(..., description="逗号分隔 symbol 列表"),
    interval: str = Query("5m"),
    redis_cache=Depends(get_redis_cache),
):
    syms = {s.strip() for s in symbols.split(",") if s.strip()}
    if not syms:
        return StreamingResponse(
            _empty_gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return StreamingResponse(
        _stream_gen(syms, interval, redis_cache),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/bars/{symbol}/{interval}")
async def sse_bars(symbol: str, interval: str, redis_cache=Depends(get_redis_cache)):
    syms = {symbol}
    return StreamingResponse(
        _stream_gen(syms, interval, redis_cache),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _empty_gen():
    yield _sse_event("ping", {"error": "no symbols"})
