from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketRuleEvent:
    level: str
    category: str
    title: str
    detail: str
    symbols: list[str]
    score: float

