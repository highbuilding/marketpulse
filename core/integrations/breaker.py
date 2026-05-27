"""Per-source 熔断器, 状态写 Redis hash (跨节点共享决策)。

状态机: closed → open → half_open → closed。
- 滑动窗口失败率 ≥ fail_threshold 且样本 ≥ min_samples → open
- open 持续 open_duration_seconds → half_open (放 1 个探针)
- half_open 探针成功 → closed; 失败 → open

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.3.1
"""
from __future__ import annotations

import time
from enum import Enum

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache

log = structlog.get_logger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def time_now() -> float:
    """间接层用于测试 monkeypatch (time.time 直接 patch 影响 fakeredis 内部)。"""
    return time.time()


class SourceBreaker:
    """每个 source (sina/em/ths) 一个实例。状态共享于 Redis hash。

    Hash fields (state:source:{name}):
      state: closed/open/half_open
      opened_at: float (state=open 时记录)
      window_start: float (滑窗起点)
      success_count, failure_count: int (当前窗口)
    """

    def __init__(
        self,
        *,
        source: str,
        cache: RedisCache,
        fail_threshold: float = 0.6,
        min_samples: int = 5,
        window_seconds: float = 60.0,
        open_duration_seconds: float = 300.0,
    ) -> None:
        self.source = source
        self._cache = cache
        self._key = keys.state_source(source)
        self._fail_threshold = fail_threshold
        self._min_samples = min_samples
        self._window_seconds = window_seconds
        self._open_duration_seconds = open_duration_seconds

    async def state(self) -> BreakerState:
        record = await self._read()
        return self._effective_state(record)

    async def allow(self) -> bool:
        s = await self.state()
        return s in (BreakerState.CLOSED, BreakerState.HALF_OPEN)

    async def report(self, *, success: bool) -> None:
        record = await self._read()
        s = self._effective_state(record)
        now = time_now()
        if s == BreakerState.HALF_OPEN:
            if success:
                await self._write({
                    "state": BreakerState.CLOSED.value,
                    "window_start": now,
                    "success_count": 0,
                    "failure_count": 0,
                    "opened_at": 0.0,
                })
                log.info("breaker.closed", source=self.source)
            else:
                await self._write({
                    "state": BreakerState.OPEN.value,
                    "opened_at": now,
                    "window_start": now,
                    "success_count": 0,
                    "failure_count": 0,
                })
                log.warning("breaker.opened", source=self.source, reason="half_open_probe_failed")
            return
        # closed: 累计在滑动窗口内
        window_start = float(record.get("window_start", now))
        if now - window_start > self._window_seconds:
            window_start = now
            success_count = 0
            failure_count = 0
        else:
            success_count = int(record.get("success_count", 0))
            failure_count = int(record.get("failure_count", 0))
        if success:
            success_count += 1
        else:
            failure_count += 1
        total = success_count + failure_count
        rate = failure_count / total if total else 0.0
        if total >= self._min_samples and rate >= self._fail_threshold:
            await self._write({
                "state": BreakerState.OPEN.value,
                "opened_at": now,
                "window_start": window_start,
                "success_count": success_count,
                "failure_count": failure_count,
            })
            log.warning("breaker.opened", source=self.source,
                        rate=round(rate, 3), samples=total)
        else:
            await self._write({
                "state": BreakerState.CLOSED.value,
                "window_start": window_start,
                "success_count": success_count,
                "failure_count": failure_count,
                "opened_at": float(record.get("opened_at", 0.0)),
            })

    def _effective_state(self, record: dict) -> BreakerState:
        s = record.get("state", BreakerState.CLOSED.value)
        if s == BreakerState.OPEN.value:
            opened_at = float(record.get("opened_at", 0.0))
            if time_now() - opened_at >= self._open_duration_seconds:
                return BreakerState.HALF_OPEN
            return BreakerState.OPEN
        return BreakerState.CLOSED if s == BreakerState.CLOSED.value else BreakerState.HALF_OPEN

    async def _read(self) -> dict:
        keys.validate(self._key)
        raw = await self._cache._r.hgetall(self._key)
        if not raw:
            return {}
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else k
            vv = v.decode() if isinstance(v, bytes) else v
            out[kk] = vv
        return out

    async def _write(self, fields: dict) -> None:
        keys.validate(self._key)
        await self._cache._r.hset(self._key, mapping={k: str(v) for k, v in fields.items()})
