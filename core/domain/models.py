from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

Market = Literal["ashare", "hk", "us", "crypto"]
HealthState = Literal["ok", "degraded", "disabled", "down"]

@dataclass(frozen=True, slots=True)
class Quote:
    market: Market
    symbol: str
    ts: datetime
    price: Decimal
    change_pct: float
    volume: int
    source: str
    def __post_init__(self) -> None:
        if self.price < 0:
            raise ValueError(f"price must be >= 0, got {self.price}")

@dataclass(frozen=True, slots=True)
class Bar:
    market: Market
    symbol: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    interval: str
    amount: float | None = None
    turnover: float | None = None
    outstanding_share: float | None = None
    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low}")

@dataclass(frozen=True, slots=True)
class Fundamental:
    symbol: str
    pe_ttm: float | None = None
    pb: float | None = None
    ev_ebitda: float | None = None
    market_cap: float | None = None
    industry: str | None = None

@dataclass(frozen=True, slots=True)
class HealthStatus:
    name: str
    state: HealthState
    detail: str | None = None
    def is_ok(self) -> bool:
        return self.state == "ok"


@dataclass(frozen=True, slots=True)
class Watchlist:
    id: int
    name: str
    is_archived: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    watchlist_id: int
    symbol: str
    added_at: datetime


@dataclass(frozen=True, slots=True)
class Sector:
    name: str
    classification: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SectorConstituent:
    sector_name: str
    symbol: str


@dataclass(frozen=True, slots=True)
class FundFlowSnapshot:
    """一次时间点的资金流,subject 可以是 symbol、sector_name 或 'north'。"""
    subject: str
    kind: Literal["symbol", "sector", "north"]
    ts: datetime
    main_net: float | None = None
    super_large_net: float | None = None
    large_net: float | None = None
    medium_net: float | None = None
    small_net: float | None = None
    pct_change: float | None = None
    hgt_net: float | None = None
    sgt_net: float | None = None


@dataclass(frozen=True, slots=True)
class IndicatorSignal:
    """指标信号事件(目前 indicator='CD', 后续可扩展 TT/NX)。"""
    symbol: str
    interval: str            # '60m' | '4h' | '1d'
    indicator: str           # 'CD'
    signal_type: Literal["buy", "sell"]
    bar_ts: datetime
    detected_at: datetime
    price: float
    d_value: float | None = None
    acknowledged: bool = False
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ChipSummary:
    """东方财富日线筹码分布摘要。"""
    symbol: str
    trade_date: datetime
    profit_ratio: float | None
    avg_cost: float | None
    cost_90_low: float | None
    cost_90_high: float | None
    concentration_90: float | None
    cost_70_low: float | None
    cost_70_high: float | None
    concentration_70: float | None
