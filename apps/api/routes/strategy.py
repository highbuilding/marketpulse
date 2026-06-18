from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_strategy_backtest_service
from core.domain.models import StrategyBacktestRun, StrategyBacktestTrade, TradeInstruction
from core.services.strategy_backtest_service import StrategyBacktestService

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


class StrategyRunDTO(BaseModel):
    id: int | None
    market: str
    strategy_id: str
    strategy_name: str
    description: str
    horizon: str
    status: str
    engine: str
    sample_start: str | None
    sample_end: str | None
    symbol_count: int
    trade_count: int
    win_rate: float | None
    avg_return_pct: float | None
    median_return_pct: float | None
    max_drawdown_pct: float | None
    worst_trade_pct: float | None
    avg_holding_days: float | None
    annual_return_pct: float | None
    total_return_pct: float | None
    sandbox_eligible: bool
    metrics: dict[str, Any]
    returns: dict[str, Any]
    yearly: list[dict[str, Any]]
    market_state: list[dict[str, Any]]
    theme_state: list[dict[str, Any]]
    exit_comparison: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    data_gaps: list[str]
    generated_at: str | None


class StrategyTradeDTO(BaseModel):
    id: int | None
    symbol: str
    name: str | None
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    holding_days: int
    exit_reason: str
    evidence: dict[str, Any]


class StrategyListResponse(BaseModel):
    market: str
    items: list[StrategyRunDTO]
    meta: dict[str, Any] = {}


class StrategyReportResponse(BaseModel):
    market: str
    strategy: StrategyRunDTO
    trades: list[StrategyTradeDTO]


class TradeInstructionDTO(BaseModel):
    id: int | None
    market: str
    instruction_key: str
    action: str
    target_type: str
    target_id: str
    target_name: str | None
    candidate_key: str | None
    title: str
    summary: str
    confidence: float
    severity: str
    reasons: list[str]
    risks: list[str]
    entry_conditions: list[str]
    invalidation_conditions: list[str]
    evidence: dict[str, Any]
    generated_at: str | None
    expires_at: str | None
    status: str


class TradeInstructionsResponse(BaseModel):
    market: str
    items: list[TradeInstructionDTO]
    meta: dict[str, Any] = {}


def _run_dto(run: StrategyBacktestRun) -> StrategyRunDTO:
    return StrategyRunDTO(
        id=run.id,
        market=run.market,
        strategy_id=run.strategy_id,
        strategy_name=run.strategy_name,
        description=run.description,
        horizon=run.horizon,
        status=run.status,
        engine=run.engine,
        sample_start=run.sample_start,
        sample_end=run.sample_end,
        symbol_count=run.symbol_count,
        trade_count=run.trade_count,
        win_rate=run.win_rate,
        avg_return_pct=run.avg_return_pct,
        median_return_pct=run.median_return_pct,
        max_drawdown_pct=run.max_drawdown_pct,
        worst_trade_pct=run.worst_trade_pct,
        avg_holding_days=run.avg_holding_days,
        annual_return_pct=run.annual_return_pct,
        total_return_pct=run.total_return_pct,
        sandbox_eligible=run.sandbox_eligible,
        metrics=run.metrics or {},
        returns=run.returns or {},
        yearly=run.yearly or [],
        market_state=run.market_state or [],
        theme_state=run.theme_state or [],
        exit_comparison=run.exit_comparison or [],
        equity_curve=run.equity_curve or [],
        data_gaps=run.data_gaps or [],
        generated_at=run.generated_at.isoformat() if run.generated_at else None,
    )


def _trade_dto(trade: StrategyBacktestTrade) -> StrategyTradeDTO:
    return StrategyTradeDTO(
        id=trade.id,
        symbol=trade.symbol,
        name=trade.name,
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        return_pct=trade.return_pct,
        holding_days=trade.holding_days,
        exit_reason=trade.exit_reason,
        evidence=trade.evidence or {},
    )


def _instruction_dto(item: TradeInstruction) -> TradeInstructionDTO:
    return TradeInstructionDTO(
        id=item.id,
        market=item.market,
        instruction_key=item.instruction_key,
        action=item.action,
        target_type=item.target_type,
        target_id=item.target_id,
        target_name=item.target_name,
        candidate_key=item.candidate_key,
        title=item.title,
        summary=item.summary,
        confidence=item.confidence,
        severity=item.severity,
        reasons=item.reasons or [],
        risks=item.risks or [],
        entry_conditions=item.entry_conditions or [],
        invalidation_conditions=item.invalidation_conditions or [],
        evidence=item.evidence or {},
        generated_at=item.generated_at.isoformat() if item.generated_at else None,
        expires_at=item.expires_at.isoformat() if item.expires_at else None,
        status=item.status,
    )


@router.get("/backtests", response_model=StrategyListResponse)
async def strategy_backtests(
    market: str = Query("ashare"),
    service: StrategyBacktestService = Depends(get_strategy_backtest_service),
) -> StrategyListResponse:
    if market != "ashare":
        return StrategyListResponse(
            market=market, items=[], meta={"stale": True, "reason": "ashare_only"})
    items = await service.list_runs(market)
    return StrategyListResponse(
        market=market,
        items=[_run_dto(i) for i in items],
        meta={"stale": not bool(items), "reason": None if items else "no_backtest_runs"},
    )


@router.get("/backtests/{strategy_id}", response_model=StrategyReportResponse)
async def strategy_backtest_report(
    strategy_id: str,
    market: str = Query("ashare"),
    service: StrategyBacktestService = Depends(get_strategy_backtest_service),
) -> StrategyReportResponse:
    run, trades = await service.get_report(market, strategy_id)
    if run is None:
        raise HTTPException(status_code=404, detail="strategy backtest not found")
    return StrategyReportResponse(
        market=market,
        strategy=_run_dto(run),
        trades=[_trade_dto(t) for t in trades],
    )


@router.get("/instructions", response_model=TradeInstructionsResponse)
async def strategy_instructions(
    market: str = Query("ashare"),
    service: StrategyBacktestService = Depends(get_strategy_backtest_service),
) -> TradeInstructionsResponse:
    if market != "ashare":
        return TradeInstructionsResponse(
            market=market, items=[], meta={"stale": True, "reason": "ashare_only"})
    items = await service.list_instructions(market)
    return TradeInstructionsResponse(
        market=market,
        items=[_instruction_dto(i) for i in items],
        meta={"stale": not bool(items), "reason": None if items else "no_active_instructions"},
    )
