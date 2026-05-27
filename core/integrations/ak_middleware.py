"""ak_call 的依赖注入容器。

collector 启动时 setup() 注入 OutletPool / Breaker map / Ratelimit map,
ak_call 调用时从容器拿。

api 进程不调 ak_call, 所以注入是 None 也能跑 — 但 api 进程不应该走到 ak_call,
任何调用都说明 read 路径未切换 (Plan 3 才解决)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.3
"""
from __future__ import annotations

from dataclasses import dataclass

from core.integrations.breaker import SourceBreaker
from core.integrations.outlets import OutletPool
from core.integrations.ratelimit import RedisTokenBucket


@dataclass
class AkMiddleware:
    outlet_pool: OutletPool
    breakers: dict[str, SourceBreaker]   # source -> SourceBreaker
    ratelimits: dict[str, RedisTokenBucket]   # source -> RedisTokenBucket


_container: AkMiddleware | None = None


def setup(middleware: AkMiddleware) -> None:
    global _container
    _container = middleware


def get() -> AkMiddleware | None:
    return _container


def reset() -> None:
    """测试用 — 清空容器以隔离测试。"""
    global _container
    _container = None
