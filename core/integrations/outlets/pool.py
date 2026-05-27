"""OutletPool — 在多个 Outlet 间路由, banned 自动 cooling N 分钟。

状态记录在 Redis (state:outlet:{id}), 跨节点共享决策。

参考: §4.3.3
"""
from __future__ import annotations

import time
from typing import Sequence

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.integrations.outlets.base import Outcome, Outlet, OutletLease

log = structlog.get_logger(__name__)


def time_now() -> float:
    return time.time()


class OutletPool:
    def __init__(
        self,
        outlets: Sequence[Outlet],
        *,
        cache: RedisCache,
        cooling_seconds: float = 1800.0,
    ) -> None:
        if not outlets:
            raise ValueError("OutletPool requires at least 1 Outlet")
        self._outlets = list(outlets)
        self._cache = cache
        self._cooling_seconds = cooling_seconds
        self._next_idx = 0  # round-robin

    async def acquire(self) -> OutletLease:
        """轮询挑一个未 cooling 的 outlet。全 cooling raise RuntimeError。"""
        n = len(self._outlets)
        for _ in range(n):
            idx = self._next_idx % n
            self._next_idx += 1
            outlet = self._outlets[idx]
            if not await self._is_cooling(outlet.name):
                lease = await outlet.acquire()
                return lease
        raise RuntimeError("no usable outlet (all banned/cooling)")

    async def report(self, lease: OutletLease, outcome: Outcome) -> None:
        outlet = self._find(lease.outlet_id)
        if outlet is not None:
            await outlet.report(lease, outcome)
        if outcome == Outcome.banned:
            await self._mark_cooling(lease.outlet_id)
            log.warning("outlet.banned", outlet=lease.outlet_id,
                        cooling_seconds=self._cooling_seconds)

    def _find(self, outlet_id: str) -> Outlet | None:
        for o in self._outlets:
            if o.name == outlet_id:
                return o
        return None

    async def _is_cooling(self, outlet_id: str) -> bool:
        key = keys.state_outlet(outlet_id)
        keys.validate(key)
        raw = await self._cache._r.hget(key, "banned_until")
        if raw is None:
            return False
        try:
            until = float(raw.decode() if isinstance(raw, bytes) else raw)
        except (TypeError, ValueError):
            return False
        return time_now() < until

    async def _mark_cooling(self, outlet_id: str) -> None:
        key = keys.state_outlet(outlet_id)
        keys.validate(key)
        until = time_now() + self._cooling_seconds
        await self._cache._r.hset(key, mapping={"banned_until": str(until)})
