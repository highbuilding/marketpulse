"""LocalOutlet — 不走代理, 直连本机网络出口。

acquire() 总是成功, env 为空 (子进程不注入 HTTP_PROXY)。
report() noop — 单一本地出口没什么状态可记录。
"""
from __future__ import annotations

from core.integrations.outlets.base import Outcome, Outlet, OutletLease


class LocalOutlet:
    def __init__(self, name: str = "local") -> None:
        self.name = name

    async def acquire(self) -> OutletLease:
        return OutletLease(outlet_id=self.name, env={})

    async def report(self, lease: OutletLease, outcome: Outcome) -> None:
        # 本地出口不分别管理状态, 由 SourceBreaker 在更高层兜
        return
