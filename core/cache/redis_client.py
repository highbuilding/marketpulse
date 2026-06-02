"""Async Redis 客户端封装。

提供 msgpack 编解码 + key 命名校验 + ping 健康检查。
所有热缓存读写经过这里,绝不直接 .get/.set 字符串。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.6
"""
from __future__ import annotations

import os
from typing import Any

import ormsgpack
import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)


class RedisCache:
    """读写均走 msgpack 序列化。Redis 客户端注入,方便测试用 fakeredis 替换。"""

    def __init__(self, redis: AsyncRedis) -> None:
        self._r = redis

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception as e:  # noqa: BLE001
            log.warning("redis.ping_failed", error=str(e))
            return False

    async def get_msgpack(self, key: str) -> Any | None:
        keys.validate(key)
        raw = await self._r.get(key)
        if raw is None:
            return None
        return ormsgpack.unpackb(raw)

    async def set_msgpack(self, key: str, value: Any, *, ttl_s: int) -> None:
        keys.validate(key)
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be > 0, got {ttl_s}")
        raw = ormsgpack.packb(value)
        await self._r.set(key, raw, ex=ttl_s)

    async def ttl(self, key: str) -> int:
        keys.validate(key)
        return int(await self._r.ttl(key))

    async def delete(self, key: str) -> None:
        keys.validate(key)
        await self._r.delete(key)


def make_redis(url: str = "redis://127.0.0.1:6379/0") -> AsyncRedis:
    """单例工厂。生产中由依赖注入使用,测试时直接 new fakeredis。

    socket_timeout=None: redis-py 8.x 默认改为 5s, 会导致 XREADGROUP block=5s 超时。
    显式恢复为 None (无超时), 让 XREADGROUP 自然等待到 block_ms 后返回空。
    health_check_interval=30: 每 30s 对空闲连接发 PING, 防止网络中间设备断开长连接。
    """
    return AsyncRedis.from_url(
        url, decode_responses=False,
        socket_timeout=None,
        socket_connect_timeout=5,
        health_check_interval=30,
        max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
    )
