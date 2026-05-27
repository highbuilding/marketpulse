"""Redis-backed 令牌桶 (纯 Lua, 原子)。

设计:
- 一次 EVAL 完成 "看桶有没有 token / 没有就告诉我多久能续上"
- 状态: hash field {tokens: float, last: float}  (last = unix seconds 上次操作时刻)
- 阻塞模式: 拿不到 token 就 sleep wait_ms 后重试 (loop 至多 max_wait_s)

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.3.2
"""
from __future__ import annotations

import asyncio
import time

import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)


# Lua 脚本: 原子取 N 个 token
# KEYS[1] = bucket key
# ARGV[1] = rate (tokens/sec)
# ARGV[2] = burst (max bucket capacity)
# ARGV[3] = n  (request token count)
# ARGV[4] = now (unix seconds, float)
#
# 返回:
#   {1, 0}  = 拿到, 等待毫秒 = 0
#   {0, ms} = 没拿到, 还需等 ms 毫秒
_LUA_ACQUIRE = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local n = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then
  tokens = burst
  last = now
end

local elapsed = math.max(0, now - last)
tokens = math.min(burst, tokens + elapsed * rate)

if tokens >= n then
  tokens = tokens - n
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'last', now)
  redis.call('EXPIRE', KEYS[1], 3600)
  return {1, 0}
else
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'last', now)
  redis.call('EXPIRE', KEYS[1], 3600)
  local needed = n - tokens
  local wait_ms = math.ceil(needed / rate * 1000)
  return {0, wait_ms}
end
"""


class RedisTokenBucket:
    def __init__(
        self,
        *,
        redis: AsyncRedis,
        key: str,
        rate: float,
        burst: int,
    ) -> None:
        keys.validate(key)
        self._r = redis
        self._key = key
        self._rate = rate
        self._burst = burst
        self._script = redis.register_script(_LUA_ACQUIRE)

    async def acquire(self, n: int = 1, *, blocking: bool = True, max_wait_s: float = 30.0) -> int:
        """成功返回 0, blocking=False 且不够时返回需要等待的毫秒数。

        blocking=True 模式下持续等待至最多 max_wait_s 秒, 超时 raise TimeoutError。
        """
        deadline = time.monotonic() + max_wait_s if blocking else None
        while True:
            now = time.time()
            ok, wait_ms = await self._script(keys=[self._key],
                                              args=[self._rate, self._burst, n, now])
            if int(ok) == 1:
                return 0
            wait_ms = int(wait_ms)
            if not blocking:
                return wait_ms
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"ratelimit timeout key={self._key} waited>{max_wait_s}s")
            await asyncio.sleep(min(wait_ms / 1000.0, 1.0))
