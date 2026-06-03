"""订阅 bus:bars.updated, final=true + 信号周期 → scan_symbol_readonly。

scan 作为下游消费者: 不拉数据 / 不聚合 / 不写 bar, 只读已存 bar 算信号。
根除 60m/4h close/open 偏移(原 cron scan 走 fetch_fresh_bars 现聚合)。
"""
from __future__ import annotations

import json

import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys
from core.domain.intervals import SIGNAL_INTERVALS_SET

log = structlog.get_logger(__name__)

_GROUP = "signal_scan"


def should_scan(payload: dict) -> bool:
    """final=true(收线)且 interval 属信号周期(15m/30m/60m/4h/1d)才扫。"""
    return bool(payload.get("final")) and payload.get("interval") in SIGNAL_INTERVALS_SET


async def _ensure_group(redis: AsyncRedis, stream: str) -> None:
    try:
        await redis.xgroup_create(stream, _GROUP, id="$", mkstream=True)
    except Exception as e:  # noqa: BLE001
        if "BUSYGROUP" not in str(e):
            raise


async def run_signal_scan_consumer(
    redis: AsyncRedis, *, consumer_id: str, scan_fn, market: str | None = None,
) -> None:
    """长循环消费 bus:bars.updated。

    scan_fn(symbol, interval) -> awaitable int(scan_symbol_readonly)。
    market 非空时只处理该市场事件(各 collector 各扫自己市场)。
    被 cancel 时干净退出。
    """
    stream = keys.BUS_BARS_UPDATED
    await _ensure_group(redis, stream)
    log.info("signal_scan_consumer.start", consumer=consumer_id, market=market)
    while True:
        try:
            entries = await redis.xreadgroup(
                _GROUP, consumer_id, {stream: ">"}, count=50, block=5000,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("signal_scan_consumer.read_failed", error=str(e))
            continue
        for _stream, msgs in entries or []:
            for msg_id, fields in msgs:
                try:
                    payload = json.loads(fields[b"data"])
                    if (market is None or payload.get("market") == market) \
                            and should_scan(payload):
                        await scan_fn(payload["symbol"], payload["interval"])
                except Exception as e:  # noqa: BLE001
                    log.warning("signal_scan_consumer.handle_failed", error=str(e))
                finally:
                    try:
                        await redis.xack(stream, _GROUP, msg_id)
                    except Exception:  # noqa: BLE001
                        pass
