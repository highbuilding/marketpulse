"""SSE: K 线增量 push.

GET /api/sse/bars/{symbol}/{interval}

事件:
- init  (历史 N 根 + 当前进行中 bar 快照)
- bar   (k.x=true, replace 末根)
- tick  (k.x=false, 原地更新末根 OHLC/volume)
- ping  (心跳 30s)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])

GROUP = "sse"
PING_INTERVAL_S = 30


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


@router.get("/bars/{symbol}/{interval}")
async def sse_bars(
    symbol: str,
    interval: str,
    redis_cache=Depends(get_redis_cache),
):
    market = infer_market(symbol)

    async def gen():
        # init: 只发当前进行中 bar (历史由 REST /bars/history 分页负责, 两通道解耦)。
        # SSE 回归实时本职 —— 不再扛历史展示, 避免被 200 根上限钳死。
        bars_data: list[dict] = []
        try:
            current = await redis_cache.get_msgpack(
                keys.cache_bars_current(market, symbol, interval),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("sse.current_read_failed", symbol=symbol, interval=interval, error=str(e))
            current = None
        if current:
            bars_data.append(current)
        yield _sse_event(
            "init",
            {
                "bars": bars_data,
                "server_ts": datetime.now(timezone.utc).isoformat(),
            },
        )

        # 订阅 bus:bars.updated
        consumer_id = f"sse-{uuid.uuid4().hex[:8]}"
        try:
            await redis_cache._r.xgroup_create(
                keys.BUS_BARS_UPDATED, GROUP, id="$", mkstream=True,
            )
        except Exception as e:  # noqa: BLE001
            if "BUSYGROUP" not in str(e):
                log.warning("sse.group_create_failed", error=str(e))

        last_ping = datetime.now(timezone.utc)
        while True:
            try:
                entries = await redis_cache._r.xreadgroup(
                    GROUP,
                    consumer_id,
                    streams={keys.BUS_BARS_UPDATED: ">"},
                    count=10,
                    block=PING_INTERVAL_S * 1000,
                )
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.warning("sse.read_failed", error=str(e))
                await asyncio.sleep(1)
                continue

            now = datetime.now(timezone.utc)
            if entries:
                for _stream, msgs in entries:
                    for msg_id, fields in msgs:
                        try:
                            data_raw = fields.get(b"data") or fields.get("data")
                            if data_raw is None:
                                continue
                            if isinstance(data_raw, bytes):
                                data_raw = data_raw.decode("utf-8", errors="replace")
                            payload = json.loads(data_raw)
                            if (
                                payload.get("symbol") == symbol
                                and payload.get("interval") == interval
                            ):
                                event = "bar" if payload.get("final") else "tick"
                                yield _sse_event(event, payload)
                                last_ping = now
                        except asyncio.CancelledError:
                            return
                        except Exception as e:  # noqa: BLE001
                            log.warning("sse.msg_parse_failed", error=str(e))
                        finally:
                            try:
                                await redis_cache._r.xack(
                                    keys.BUS_BARS_UPDATED, GROUP, msg_id,
                                )
                            except Exception as e:  # noqa: BLE001
                                log.warning("sse.xack_failed", error=str(e))

            if (now - last_ping).total_seconds() >= PING_INTERVAL_S:
                yield _sse_event("ping", {"server_ts": now.isoformat()})
                last_ping = now

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
