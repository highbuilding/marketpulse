"""SSE: K 线增量 push.

GET /api/sse/bars/{symbol}/{interval}         单标的
GET /api/sse/bars/batch?symbols=A,B,C&interval=5m  多标的 (批量, 1 连接)

事件:
- connected (握手成功)
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
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])

PING_INTERVAL_S = 30
_REGISTER_REFRESH_S = 60   # 订阅登记表续期周期(< TTL 120s, 防活跃标的过期)


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _register_subscriptions(symbols: set[str], interval: str, redis_cache) -> None:
    """把'谁在看'写进订阅登记表 (TTL 120s, 边看边续期)。

    collector 的进行中态 ticker / 分时 writer / poller 扫这张表决定给哪些标的算实时。
    每个 symbol 按其市场写 state:subscribe:{market}:{symbol}:{interval}。失败仅忽略(优雅降级)。
    """
    for sym in symbols:
        try:
            market = infer_market(sym)
            await redis_cache._r.set(  # noqa: SLF001
                keys.state_bar_subscription(market, sym, interval), b"1", ex=120)
        except Exception as e:  # noqa: BLE001
            log.warning("sse.register_sub_failed", symbol=sym, error=str(e))


async def _stream_gen(symbols: set[str], interval: str, hub, redis_cache):
    """注册 hub → 先发 connected + init 快照 → 循环取队列(超时发 ping)→ finally 注销。"""
    keys_list = [(s, interval) for s in symbols]
    sub = hub.register(keys_list)           # 先注册, 消除 init 与订阅间漏窗口
    try:
        server_ts = datetime.now(timezone.utc).isoformat()
        yield _sse_event("connected", {"symbols": list(symbols),
                                       "interval": interval, "server_ts": server_ts})
        # 写订阅登记表, 激活 collector 进行中态 ticker / 分时 writer
        await _register_subscriptions(symbols, interval, redis_cache)
        last_reg = datetime.now(timezone.utc)
        for sym in symbols:
            try:
                cur = await redis_cache.get_msgpack(
                    keys.cache_bars_current(infer_market(sym), sym, interval))
            except Exception:  # noqa: BLE001
                cur = None
            if cur:
                yield _sse_event("init", {"bars": [cur], "symbol": sym, "server_ts": server_ts})
        while True:
            try:
                payload = await asyncio.wait_for(sub.get(), timeout=PING_INTERVAL_S)
                got_msg = True
            except asyncio.TimeoutError:
                got_msg = False
            except asyncio.CancelledError:
                return
            now = datetime.now(timezone.utc)
            # 续期订阅登记表: 每 60s 一次, 不论有无消息(订阅 TTL 120s)。
            # 关键: 活跃推送的标的消息不断、永不超时, 必须在消息路径也续期, 否则
            # 2 分钟后 state:subscribe 过期 → collector 停掉该标的实时(回归)。
            if (now - last_reg).total_seconds() >= _REGISTER_REFRESH_S:
                await _register_subscriptions(symbols, interval, redis_cache)
                last_reg = now
            if not got_msg:
                yield _sse_event("ping", {"server_ts": now.isoformat()})
                continue
            event = "bar" if payload.get("final") else "tick"
            yield _sse_event(event, payload)
    finally:
        hub.unregister(keys_list, sub)


@router.get("/bars/batch")
async def sse_bars_batch(
    request: Request,
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
    hub = request.app.state.bars_hub
    return StreamingResponse(
        _stream_gen(syms, interval, hub, redis_cache),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/bars/{symbol}/{interval}")
async def sse_bars(
    request: Request,
    symbol: str,
    interval: str,
    redis_cache=Depends(get_redis_cache),
):
    hub = request.app.state.bars_hub
    return StreamingResponse(
        _stream_gen({symbol}, interval, hub, redis_cache),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _empty_gen():
    yield _sse_event("ping", {"error": "no symbols"})
