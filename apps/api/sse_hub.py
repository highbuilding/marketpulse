"""SSE 单读多分发 hub: 每 worker 一个 run() 读 Redis 流, 解析一次按 symbol 分发。

把"每连接各自 xread 全局流再过滤"(O(连接×消息))降到 O(消息);
Redis 连接 = 每 hub 1 条。多 worker: 各 worker 独立 hub 从 $ 读全量。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Hashable, Iterable

import structlog

log = structlog.get_logger(__name__)

DEFAULT_QUEUE_MAX = 100
_BATCH = 50
_BLOCK_MS = 1000


class Subscriber:
    """单 SSE 连接的收件箱: 有界队列, 满则丢最旧(进行中态丢帧无害, 绝不阻塞 hub)。"""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_MAX) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def offer(self, item: dict) -> None:
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def get(self) -> dict:
        return await self._q.get()


class StreamHub:
    def __init__(self, redis, channel: str, key_fn: Callable[[dict], Hashable]) -> None:
        self._redis = redis
        self._channel = channel
        self._key_fn = key_fn
        self._registry: dict[Hashable, set[Subscriber]] = {}
        self._stopped = False

    def register(self, keys: Iterable[Hashable], maxsize: int = DEFAULT_QUEUE_MAX) -> Subscriber:
        sub = Subscriber(maxsize)
        for k in keys:
            self._registry.setdefault(k, set()).add(sub)
        return sub

    def unregister(self, keys: Iterable[Hashable], sub: Subscriber) -> None:
        for k in keys:
            s = self._registry.get(k)
            if s:
                s.discard(sub)
                if not s:
                    self._registry.pop(k, None)

    def dispatch(self, payload: dict) -> int:
        try:
            key = self._key_fn(payload)
        except Exception:  # noqa: BLE001
            return 0
        subs = self._registry.get(key)
        if not subs:
            return 0
        for sub in list(subs):
            sub.offer(payload)
        return len(subs)

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        """单读循环: xread 全量 → 解析一次 → dispatch。每 worker 一个。"""
        log.info("sse_hub.started", channel=self._channel)
        last_id = "$"
        while not self._stopped:
            try:
                entries = await self._redis._r.xread(  # noqa: SLF001
                    streams={self._channel: last_id}, count=_BATCH, block=_BLOCK_MS)
            except asyncio.CancelledError:
                log.info("sse_hub.cancelled", channel=self._channel)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("sse_hub.read_failed", channel=self._channel, error=str(e))
                await asyncio.sleep(1)
                continue
            if not entries:
                continue
            for _stream, msgs in entries:
                for msg_id, fields in msgs:
                    last_id = msg_id
                    try:
                        raw = fields.get(b"data") or fields.get("data")
                        if raw is None:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        self.dispatch(json.loads(raw))
                    except asyncio.CancelledError:
                        return
                    except Exception as e:  # noqa: BLE001
                        log.warning("sse_hub.parse_failed",
                                    channel=self._channel, error=str(e))


def bars_key(payload: dict):
    return (payload.get("symbol"), payload.get("interval"))


def intraday_key(payload: dict):
    return payload.get("symbol")
