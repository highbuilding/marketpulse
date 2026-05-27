from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketSymbolSnapshot:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float


@dataclass(frozen=True, slots=True)
class MarketSectorSnapshot:
    code: str
    name: str
    change_pct: float
    company_count: int
    leader_name: str
    leader_change_pct: float
    leader_symbol: str | None = None

