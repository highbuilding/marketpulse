"""实盘消息 consumer: 消费行情/信号事件,写 SQLite 后发布实时消息。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys
from core.domain.models import LiveMessage
from core.persistence.live_message_repo import LiveMessageRepo
from core.services.live_message_service import LiveMessageService

log = structlog.get_logger(__name__)

_GROUP = "live_message"
_STREAMS = (
    keys.BUS_QUOTE_TICK,
    keys.BUS_BARS_UPDATED,
    keys.BUS_SIGNAL_NEW,
)


async def _ensure_group(redis: AsyncRedis, stream: str) -> None:
    try:
        await redis.xgroup_create(stream, _GROUP, id="$", mkstream=True)
    except Exception as e:  # noqa: BLE001
        if "BUSYGROUP" not in str(e):
            raise


def _decode_stream(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _decode_msg_id(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _decode_payload(fields: dict) -> dict:
    raw = fields.get(b"data") or fields.get("data")
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return json.loads(raw)


def _message_payload(message: LiveMessage) -> dict:
    payload = asdict(message)
    for key in ("ts", "created_at"):
        if isinstance(payload.get(key), datetime):
            payload[key] = payload[key].isoformat()
    return payload


async def _publish(redis: AsyncRedis, messages: list[LiveMessage]) -> None:
    for msg in messages:
        payload = _message_payload(msg)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        await redis.lpush(keys.cache_live_messages(msg.market), raw)
        await redis.ltrim(keys.cache_live_messages(msg.market), 0, 199)
        await redis.xadd(
            keys.BUS_LIVE_MESSAGE,
            {"data": raw.encode()},
            maxlen=10000,
            approximate=True,
        )


async def run_live_message_consumer(
    redis: AsyncRedis,
    *,
    consumer_id: str,
    service: LiveMessageService,
    repo: LiveMessageRepo,
    market: str | None = None,
) -> None:
    for stream in _STREAMS:
        await _ensure_group(redis, stream)
    log.info("live_message_consumer.start", consumer=consumer_id, market=market)
    streams = {stream: ">" for stream in _STREAMS}
    while True:
        try:
            entries = await redis.xreadgroup(
                _GROUP, consumer_id, streams, count=100, block=5000,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("live_message_consumer.read_failed", error=str(e))
            continue

        for stream_raw, msgs in entries or []:
            stream = _decode_stream(stream_raw)
            for msg_id_raw, fields in msgs:
                msg_id = _decode_msg_id(msg_id_raw)
                try:
                    payload = _decode_payload(fields)
                    if market is not None and payload.get("market") != market:
                        continue
                    if stream == keys.BUS_QUOTE_TICK:
                        messages = await service.handle_quote_tick(
                            payload, source_event_id=msg_id)
                    elif stream == keys.BUS_SIGNAL_NEW:
                        messages = await service.handle_signal_new(
                            payload, source_event_id=msg_id)
                    elif stream == keys.BUS_BARS_UPDATED:
                        messages = await service.handle_bar_updated(
                            payload, source_event_id=msg_id)
                    else:
                        messages = []
                    if not messages:
                        continue
                    inserted = await repo.insert_many(messages)
                    if inserted <= 0:
                        continue
                    await _publish(redis, messages)
                    log.info("live_message.generated",
                             stream=stream, inserted=inserted, count=len(messages))
                except Exception as e:  # noqa: BLE001
                    log.warning("live_message_consumer.handle_failed",
                                stream=stream, msg_id=msg_id, error=str(e))
                finally:
                    try:
                        await redis.xack(stream, _GROUP, msg_id_raw)
                    except Exception:  # noqa: BLE001
                        pass

