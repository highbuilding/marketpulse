from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import get_daily_review_repo, get_market_conclusion_service
from core.persistence.daily_review_repo import DailyReviewRepo
from core.services.market_conclusion_service import (
    ConclusionSection,
    DailyReviewConclusion,
    IntradayConclusion,
    MarketConclusionService,
)

router = APIRouter(prefix="/api/conclusions", tags=["conclusions"])


class ConclusionSectionDTO(BaseModel):
    key: str
    title: str
    label: str
    score: float
    summary: str
    evidence: dict[str, Any]


class IntradayConclusionDTO(BaseModel):
    market: str
    generated_at: str
    window_minutes: int
    formula_version: str
    sections: list[ConclusionSectionDTO]
    data_gaps: list[str]


class DailyReviewConclusionDTO(BaseModel):
    market: str
    trade_date: str
    generated_at: str
    formula_version: str
    summary: str
    sections: list[ConclusionSectionDTO]
    next_watch: list[str]
    data_gaps: list[str]


def _section_dto(section: ConclusionSection) -> ConclusionSectionDTO:
    return ConclusionSectionDTO(
        key=section.key,
        title=section.title,
        label=section.label,
        score=section.score,
        summary=section.summary,
        evidence=section.evidence,
    )


def _intraday_dto(conclusion: IntradayConclusion) -> IntradayConclusionDTO:
    return IntradayConclusionDTO(
        market=conclusion.market,
        generated_at=conclusion.generated_at.isoformat(),
        window_minutes=conclusion.window_minutes,
        formula_version=conclusion.formula_version,
        sections=[_section_dto(s) for s in conclusion.sections],
        data_gaps=conclusion.data_gaps,
    )


def _daily_review_dto(conclusion: DailyReviewConclusion) -> DailyReviewConclusionDTO:
    return DailyReviewConclusionDTO(
        market=conclusion.market,
        trade_date=conclusion.trade_date,
        generated_at=conclusion.generated_at.isoformat(),
        formula_version=conclusion.formula_version,
        summary=conclusion.summary,
        sections=[_section_dto(s) for s in conclusion.sections],
        next_watch=conclusion.next_watch,
        data_gaps=conclusion.data_gaps,
    )


@router.get("/intraday", response_model=IntradayConclusionDTO)
async def intraday_conclusion(
    market: str = Query("ashare"),
    minutes: int = Query(60, ge=5, le=240),
    service: MarketConclusionService = Depends(get_market_conclusion_service),
) -> IntradayConclusionDTO:
    """盘中结论层:只读事实流水和题材快照,不触发外部数据源。"""
    return _intraday_dto(await service.intraday(market, minutes=minutes))


@router.get("/daily-review", response_model=DailyReviewConclusionDTO)
async def daily_review_conclusion(
    market: str = Query("ashare"),
    date: str | None = Query(None, description="交易日 YYYY-MM-DD, 缺省=本市场今天"),
    service: MarketConclusionService = Depends(get_market_conclusion_service),
) -> DailyReviewConclusionDTO:
    """每日复盘结论:优先返回 collector 已生成的存档(含日线走势/板块位置/龙头分层),
    无存档时退化为纯消息即时计算。不触发外部数据源。"""
    return _daily_review_dto(await service.daily_review(market, trade_date=date))


@router.get("/daily-review/dates")
async def daily_review_dates(
    market: str = Query("ashare"),
    limit: int = Query(250, ge=1, le=1000),
    repo: DailyReviewRepo = Depends(get_daily_review_repo),
) -> dict[str, list[str]]:
    """已生成复盘的交易日列表(降序),供前端日期下拉。"""
    return {"dates": await repo.list_dates(market, limit=limit)}
