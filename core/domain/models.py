from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

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
    amount: float | None = None  # 当日累计成交额(元), sina parts[9]
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


@dataclass(frozen=True, slots=True)
class Position:
    """用户手动维护的观察/持仓记录。"""
    market: str
    symbol: str
    name: str | None = None
    quantity: int = 0
    cost_price: float | None = None
    opened_at: datetime | None = None
    strategy_tag: str | None = None
    entry_reason: str | None = None
    status: str = "active"
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ThemeSnapshot:
    """题材/板块某一时点的摘要快照。"""
    market: str
    theme_code: str
    theme_name: str
    classification: str
    ts: datetime
    pct_change: float | None = None
    pct_change_5m: float | None = None
    amount: float | None = None
    amount_ratio: float | None = None
    up_ratio: float | None = None
    limit_up_count: int | None = None
    member_count: int | None = None
    leader_symbols: list[str] | None = None
    divergence_score: float | None = None
    support_score: float | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ThemeState:
    """题材状态机当前结果。"""
    market: str
    theme_code: str
    theme_name: str
    state: str
    score: float | None = None
    reason: str | None = None
    evidence: dict[str, Any] | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThemeMembership:
    """题材成分股及角色。"""
    market: str
    theme_code: str
    symbol: str
    name: str | None = None
    role: str | None = None
    pct_change: float | None = None
    amount: float | None = None
    volume_ratio: float | None = None
    is_above_intraday_avg: bool | None = None
    evidence: dict[str, Any] | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """程序生成的事实事件。"""
    market: str
    event_type: str
    severity: str
    subject_type: str
    subject_id: str
    title: str
    summary: str | None
    evidence: dict[str, Any]
    occurred_at: datetime
    created_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    """策略规则生成的候选机会/风险。"""
    market: str
    candidate_key: str
    symbol: str
    candidate_type: str
    decision: str
    score: float
    name: str | None = None
    theme_code: str | None = None
    theme_name: str | None = None
    reasons: list[str] | None = None
    risks: list[str] | None = None
    evidence: dict[str, Any] | None = None
    status: str = "active"
    generated_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class AITradeOpinion:
    """AI 基于候选和证据生成的交易观察结论。"""
    market: str
    opinion_key: str
    target_type: str
    target_id: str
    target_name: str | None
    decision: str
    confidence: float | None
    title: str
    thesis: str
    reasons: list[str]
    risks: list[str]
    evidence: dict[str, Any]
    source_candidate_id: int | None = None
    generated_at: datetime | None = None
    expires_at: datetime | None = None
    status: str = "active"
    id: int | None = None
