"""Collector Leader 选举 — 单 Redis SETNX 锁 + 续期。

设计: 单节点部署时永远续期成功; 多节点时抢锁, 只 leader 跑 cron job。
RTO ≤ ttl_s (默认 15s)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.2
"""
from __future__ import annotations

import asyncio
import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)


class Leader:
    def __init__(
        self,
        *,
        redis: AsyncRedis,
        node_id: str,
        ttl_s: int = 15,
        renew_interval_s: int = 5,
        lock_key: str | None = None,
    ) -> None:
        self._r = redis
        self._node_id = node_id
        self._ttl_s = ttl_s
        self._renew_interval_s = renew_interval_s
        self._key = lock_key or keys.state_leader_collector()
        self._is_leader = False
        self._stopped = False

    def is_leader(self) -> bool:
        return self._is_leader

    async def try_acquire_once(self) -> bool:
        """一轮抢锁/续期。供测试 / 启动时 warm-up 用。"""
        keys.validate(self._key)
        # 用 SET NX EX 抢: 没人持锁就成为 leader
        ok = await self._r.set(self._key, self._node_id, nx=True, ex=self._ttl_s)
        if ok:
            self._is_leader = True
            log.info("leader.acquired", node=self._node_id)
            return True
        # 已有人持锁: 看是不是自己
        current = await self._r.get(self._key)
        if current is not None:
            current_id = current.decode() if isinstance(current, bytes) else current
            if current_id == self._node_id:
                await self._r.expire(self._key, self._ttl_s)
                self._is_leader = True
                return True
        if self._is_leader:
            log.warning("leader.lost", node=self._node_id, current=current)
        self._is_leader = False
        return False

    async def acquire_loop(self) -> None:
        """长循环: 每 renew_interval_s 抢一次锁/续期。后台 task 跑。"""
        log.info("leader.loop_start", node=self._node_id,
                 ttl_s=self._ttl_s, renew=self._renew_interval_s)
        while not self._stopped:
            try:
                await self.try_acquire_once()
            except Exception as e:  # noqa: BLE001
                log.warning("leader.renew_failed", node=self._node_id, error=str(e))
            await asyncio.sleep(self._renew_interval_s)
        log.info("leader.loop_stopped", node=self._node_id)

    async def release(self) -> None:
        """主动放锁 (shutdown 时调用)。只放属于自己的锁。"""
        self._stopped = True
        current = await self._r.get(self._key)
        if current is None:
            return
        current_id = current.decode() if isinstance(current, bytes) else current
        if current_id == self._node_id:
            await self._r.delete(self._key)
            log.info("leader.released", node=self._node_id)
        self._is_leader = False
