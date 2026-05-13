from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Protocol

from core.domain.models import Bar, HealthStatus, Market, Quote


class AdapterError(Exception):
    def __init__(self, msg: str, source: str) -> None:
        super().__init__(f"[{source}] {msg}")
        self.source = source


CBState = Literal["closed", "open", "half_open"]


@dataclass
class CircuitBreaker:
    fail_threshold: int = 3
    reset_after_s: float = 300.0
    state: CBState = "closed"
    failure_count: int = 0
    opened_at: float | None = None

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if self.opened_at is not None and time.time() - self.opened_at >= self.reset_after_s:
                self.state = "half_open"
                return True
            return False
        return True

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.fail_threshold:
            self.state = "open"
            self.opened_at = time.time()

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = "closed"
        self.opened_at = None


class MarketAdapter(Protocol):
    market: Market
    name: str

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]: ...
    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None: ...
    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...
    async def health(self) -> HealthStatus: ...
