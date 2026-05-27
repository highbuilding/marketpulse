"""bus:bars.refill_request 消费者 — Plan 3 read-path 用。

api 发现 cache miss + DB 不全时 publish 到 bus, collector 在此消费, 拉数据
写库 + 写 cache + 发 bus:bars.updated。

Plan 2 仅搭骨架; refill_fn 当前用 KLineService.fetch_fresh_bars 兜, Plan 3 再优化。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.3
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)

# Consumer group 名(用同一个 group 跨节点负载均衡)
_GROUP = "collector"


RefillFn = Callable[[str, str, str, int], Awaitable[None]]
# (market, symbol, interval, days) -> None


async def handle_refill_message(message: dict, *, refill_fn: RefillFn) -> None:
    """处理一条 refill 请求。malformed/失败 不抛, 仅日志。"""
    try:
        market = message["market"]
        symbol = message["symbol"]
        interval = message["interval"]
        days = int(message.get("days", 365))
    except (KeyError, TypeError, ValueError) as e:
        log.warning("refill.malformed", message=message, error=str(e))
        return
    try:
        await refill_fn(market, symbol, interval, days)
        log.info("refill.done", market=market, symbol=symbol, interval=interval, days=days)
    except Exception as e:  # noqa: BLE001
        log.warning("refill.failed", market=market, symbol=symbol,
                    interval=interval, error=str(e))


async def ensure_group(redis: AsyncRedis, stream: str) -> None:
    """确保 consumer group 存在 (BUSYGROUP 表示已存在, 静默忽略)。"""
    try:
        await redis.xgroup_create(stream, _GROUP, id="$", mkstream=True)
    except Exception as e:  # noqa: BLE001
        if "BUSYGROUP" in str(e):
            return
        raise


async def consume_loop(
    redis: AsyncRedis,
    *,
    consumer_id: str,
    refill_fn: RefillFn,
    block_ms: int = 5000,
) -> None:
    """长循环, 阻塞读 stream。被 cancel 时清退。"""
    stream = keys.BUS_BARS_REFILL_REQUEST
    await ensure_group(redis, stream)
    log.info("refill_consumer.start", consumer=consumer_id, stream=stream)
    while True:
        try:
            entries = await redis.xreadgroup(
                _GROUP, consumer_id, streams={stream: ">"},
                count=10, block=block_ms,
            )
        except asyncio.CancelledError:
            log.info("refill_consumer.cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("refill_consumer.read_failed", error=str(e))
            await asyncio.sleep(1)
            continue
        if not entries:
            continue
        for _stream, msgs in entries:
            for msg_id, fields in msgs:
                try:
                    raw = fields.get(b"data") if isinstance(fields, dict) else None
                    if raw is None:
                        log.warning("refill_consumer.missing_data_field", msg_id=msg_id)
                        await redis.xack(stream, _GROUP, msg_id)
                        continue
                    payload = json.loads(raw)
                    await handle_refill_message(payload, refill_fn=refill_fn)
                finally:
                    await redis.xack(stream, _GROUP, msg_id)
