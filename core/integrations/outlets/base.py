"""Outlet 抽象 — 出口管理 (单 IP / 多代理 / VPN 池)。

LocalOutlet 是默认实现 (无代理直连)。
未来商业代理池作为 Outlet 子类加入, ak_call 业务代码无感知。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Outcome(str, Enum):
    ok = "ok"
    empty = "empty"
    parse_error = "parse_error"
    timeout = "timeout"
    banned = "banned"


@dataclass(frozen=True)
class OutletLease:
    outlet_id: str
    env: dict[str, str] = field(default_factory=dict)


class Outlet(Protocol):
    name: str

    async def acquire(self) -> OutletLease: ...
    async def report(self, lease: OutletLease, outcome: Outcome) -> None: ...
