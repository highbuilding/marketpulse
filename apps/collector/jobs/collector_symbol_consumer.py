from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.persistence.collector_symbol_repo import CollectorSymbolRepo

log = structlog.get_logger(__name__)


async def run_collector_symbol_consumer(
    redis,
    *,
    cache: RedisCache,
    repo: CollectorSymbolRepo,
    consumer_id: str,
    market: str,
) -> None:
    last_id = "$"
    log.info("collector_symbol_consumer.start", consumer=consumer_id, market=market)
    while True:
        try:
            rows = await redis.xread(
                {keys.BUS_COLLECTOR_SYMBOLS_CHANGED: last_id},
                count=20,
                block=5000,
            )
            for _stream, entries in rows:
                for event_id, fields in entries:
                    last_id = event_id
                    raw = fields.get(b"data") or fields.get("data")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    payload = json.loads(raw) if raw else {}
                    if payload.get("market") != market:
                        continue
                    await _ack(cache, repo, payload, market=market)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("collector_symbol_consumer.error", market=market, error=str(e))
            await asyncio.sleep(1)


async def _ack(
    cache: RedisCache,
    repo: CollectorSymbolRepo,
    payload: dict[str, Any],
    *,
    market: str,
) -> None:
    request_id = str(payload.get("request_id") or "")
    symbol = str(payload.get("symbol") or "").upper()
    if not request_id:
        return
    ok = False
    message = "collector did not load symbol"
    try:
        rows = await repo.list(market, include_disabled=True)
        current = next((r for r in rows if r.symbol == symbol), None)
        ok = current is not None or payload.get("action") == "delete"
        if current is not None:
            message = f"loaded {symbol}, enabled={current.enabled}, collect_5m={current.collect_5m}"
        else:
            message = f"{symbol} removed"
    except Exception as e:  # noqa: BLE001
        message = str(e)
    await cache.set_msgpack(
        keys.state_collector_symbol_ack(market, request_id),
        {"ok": ok, "message": message},
        ttl_s=30,
    )
    log.info("collector_symbol_consumer.ack", market=market, symbol=symbol, ok=ok)
