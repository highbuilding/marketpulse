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
    final: bool = True  # True=已收线(权威); False=进行中态(盘中入库, 收线后被覆盖翻 True)
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
class LimitPoolItem:
    """涨停/炸板/跌停池事实源。

    pool_type:
    - limit_up: 涨停股池
    - broken_limit: 炸板股池
    - down_limit: 跌停股池
    """
    market: str
    trade_date: str
    pool_type: Literal["limit_up", "broken_limit", "down_limit", "previous"]
    symbol: str
    name: str | None = None
    change_pct: float | None = None
    price: float | None = None
    limit_price: float | None = None
    amount: float | None = None
    free_float_cap: float | None = None
    total_cap: float | None = None
    turnover_rate: float | None = None
    seal_amount: float | None = None
    first_seal_time: str | None = None
    last_seal_time: str | None = None
    break_count: int | None = None
    ladder_count: int | None = None
    industry: str | None = None
    amplitude: float | None = None
    raw: dict[str, Any] | None = None
    pulled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StockChange:
    """个股盘口异动事实源 (stock_changes_em)。

    change_type 为东财异动类型中文名 (火箭发射/封涨停板/打开涨停板/竞价上涨/...)。
    change_time 为 BJT 时分秒 HH:MM:SS。change_pct 已转百分数 (9.99 表示 +9.99%)。
    """
    market: str
    trade_date: str
    change_time: str
    symbol: str
    change_type: str
    name: str | None = None
    price: float | None = None
    change_pct: float | None = None
    raw: dict[str, Any] | None = None
    pulled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BoardChange:
    """板块异动事实源 (stock_board_change_em)。快照型, 当日按 board_name 覆盖。"""
    market: str
    trade_date: str
    board_name: str
    change_pct: float | None = None
    main_net_inflow: float | None = None
    change_total: int | None = None
    top_symbol: str | None = None
    top_name: str | None = None
    top_direction: str | None = None
    raw: dict[str, Any] | None = None
    pulled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SwIndustryBar:
    """申万一级行业指数日线 (index_hist_sw)。industry_code 形如 801010 (不带 .SI)。"""
    industry_code: str
    trade_date: str
    industry_name: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    market: str = "ashare"
    pulled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SwIndustryInfo:
    """申万一级行业最新元信息 (sw_index_first_info): 成份数 + 估值。"""
    industry_code: str
    industry_name: str
    member_count: int | None = None
    pe_static: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    market: str = "ashare"
    updated_at: datetime | None = None


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
    """用户手动维护的观察/持仓记录。

    同一标的可多条(多次开/平仓), 按 id 操作。盈亏存库(B 方案):
    活跃持仓概览页用实时现价算浮动盈亏(不写库); 已平仓用手填平仓价算已实现盈亏写库。
    """
    market: str
    symbol: str
    name: str | None = None
    quantity: int = 0
    cost_price: float | None = None
    close_price: float | None = None
    profit_amount: float | None = None
    profit_pct: float | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
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
class ThemeDefinition:
    """用户可维护的题材/板块静态定义。"""
    market: str
    theme_code: str
    theme_name: str
    classification: str
    priority: str = "P2"
    enabled: bool = True
    source: str = "manual"
    seed_version: str | None = None
    note: str | None = None
    member_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThemeConstituent:
    """用户可维护的题材静态成分股。"""
    market: str
    theme_code: str
    symbol: str
    name: str | None = None
    role_hint: str | None = None
    weight: float | None = None
    enabled: bool = True
    source: str = "manual"
    seed_version: str | None = None
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CollectorSymbol:
    """采集标的清单: collector 读取的唯一采集范围。"""
    market: str
    symbol: str
    name: str | None = None
    enabled: bool = True
    source: str = "manual"
    collect_snapshot: bool = True
    collect_5m: bool = True
    collect_signals: bool = True
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LiveMessage:
    """实盘消息事实源。Redis 负责实时分发,SQLite 负责复盘。"""
    id: str
    market: str
    ts: datetime
    level: Literal["info", "watch", "warning", "critical"]
    category: Literal["index", "theme", "watchlist", "signal", "risk", "system"]
    title: str
    body: str
    source_event: str
    dedupe_key: str
    theme_code: str | None = None
    symbol: str | None = None
    symbols: list[str] | None = None
    source_event_id: str | None = None
    payload: dict[str, Any] | None = None
    rule_version: str = "v1"
    created_at: datetime | None = None


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
