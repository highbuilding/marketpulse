from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import (
    get_daily_review_repo,
    get_market_conclusion_service,
)
from core.persistence.daily_review_repo import DailyReviewRepo
from core.services.market_conclusion_service import (
    ConclusionSection,
    DailyReviewConclusion,
    IntradayConclusion,
    MarketConclusionService,
)

router = APIRouter(prefix="/api/conclusions", tags=["conclusions"])
_BJT = ZoneInfo("Asia/Shanghai")
_UTC = timezone.utc


def _in_ashare_session(now: datetime) -> bool:
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


# 30min 结论轮的 slot 边界 (BJT)。每个 slot 概括其前 30min 窗。
# 落在交易时段内的 30min 整点 (午休期间不出 slot)。
_SLOT_BOUNDARIES: list[tuple[int, int]] = [
    (10, 0), (10, 30), (11, 0), (11, 30), (13, 30), (14, 0), (14, 30), (15, 0),
]


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


class RoundSlotDTO(BaseModel):
    slot_time: str
    phase: str  # "cooled" 冷却态(已定稿) | "active" 进行态(实时)
    window_minutes: int
    sections: list[ConclusionSectionDTO]
    data_gaps: list[str] = []
    generated_at: str | None = None


class IntradayRoundsResponse(BaseModel):
    market: str
    trade_date: str
    slots: list[RoundSlotDTO] = []
    meta: dict[str, Any] = {}


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


@router.get("/intraday-rounds", response_model=IntradayRoundsResponse)
async def intraday_rounds(
    market: str = Query("ashare"),
    date: str | None = Query(None, description="交易日 YYYY-MM-DD, 缺省=今天 BJT"),
    service: MarketConclusionService = Depends(get_market_conclusion_service),
) -> IntradayRoundsResponse:
    """盘中 30min 结论轮 —— **读时按窗切片**, 无 cron 无落档。

    对当天每个已过的 30min 边界现算其前 30min 窗结论 = 冷却态;
    盘中当前未完成窗实时算 = 进行态。数据全来自已落库的 live_messages/theme_snapshots。
    非 A 股返回空 (ashare_only)。
    """
    now_bjt = datetime.now(_BJT)
    trade_date = date or now_bjt.date().isoformat()
    if market != "ashare":
        return IntradayRoundsResponse(market=market, trade_date=trade_date,
                                      meta={"stale": True, "reason": "ashare_only"})

    day = datetime.fromisoformat(trade_date).date()
    is_today = trade_date == now_bjt.date().isoformat()
    slots: list[RoundSlotDTO] = []

    # 冷却态: 每个已过边界 b 概括 [b-30min, b]。
    for hh, mm in _SLOT_BOUNDARIES:
        b_bjt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=_BJT)
        if is_today and b_bjt > now_bjt:
            break  # 未到的边界还没有 slot
        end = b_bjt.astimezone(_UTC)
        conc = await service.intraday_window(
            market, start=end - timedelta(minutes=30), end=end, window_minutes=30)
        slots.append(RoundSlotDTO(
            slot_time=f"{hh:02d}:{mm:02d}", phase="cooled",
            window_minutes=conc.window_minutes,
            sections=[_section_dto(s) for s in conc.sections],
            data_gaps=conc.data_gaps,
            generated_at=conc.generated_at.isoformat(),
        ))

    # 进行态: 仅当查今天且当前在交易时段, 实时算当前未完成窗。
    if is_today and _in_ashare_session(now_bjt):
        elapsed = (now_bjt.minute % 30) or 30
        live = await service.intraday(market, minutes=elapsed)
        slots.append(RoundSlotDTO(
            slot_time=now_bjt.strftime("%H:%M"), phase="active",
            window_minutes=live.window_minutes,
            sections=[_section_dto(s) for s in live.sections],
            data_gaps=live.data_gaps,
            generated_at=live.generated_at.isoformat(),
        ))

    return IntradayRoundsResponse(market=market, trade_date=trade_date, slots=slots,
                                  meta={"stale": False})
