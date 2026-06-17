from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from core.domain.market_calendar import is_trading_day
from core.persistence.daily_review_repo import DailyReviewRepo
from core.services.market_conclusion_service import MarketConclusionService

log = structlog.get_logger(__name__)
_BJT = ZoneInfo("Asia/Shanghai")


async def generate_and_store_daily_review(
    service: MarketConclusionService,
    repo: DailyReviewRepo,
    *,
    market: str = "ashare",
    trade_date: str | None = None,
) -> bool:
    """生成当日复盘 (日线层 + 消息层) 并落 daily_reviews。

    collector 持 BarRepo, 经 service.generate_daily_review 算日线走势/板块/龙头分层。
    任何失败只 warning, 不拖死调用链 (优雅降级)。
    """
    td = trade_date or datetime.now(_BJT).date().isoformat()
    try:
        conclusion = await service.generate_daily_review(market, trade_date=td)
        await repo.save(
            market=conclusion.market,
            trade_date=conclusion.trade_date,
            formula_version=conclusion.formula_version,
            summary=conclusion.summary,
            sections=[
                {
                    "key": s.key, "title": s.title, "label": s.label,
                    "score": s.score, "summary": s.summary, "evidence": s.evidence,
                }
                for s in conclusion.sections
            ],
            next_watch=conclusion.next_watch,
            data_gaps=conclusion.data_gaps,
            generated_at=conclusion.generated_at,
        )
        log.info("daily_review.generated", market=market, trade_date=conclusion.trade_date,
                 sections=len(conclusion.sections), gaps=len(conclusion.data_gaps))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("daily_review.generate_failed", market=market, trade_date=td, error=str(e))
        return False


async def refresh_ashare_daily_review(
    service: MarketConclusionService,
    repo: DailyReviewRepo,
) -> None:
    """收盘后生成当日 A 股复盘 (cron 调用)。非交易日跳过。"""
    now = datetime.now(_BJT)
    if not is_trading_day("ashare", now):
        return
    await generate_and_store_daily_review(service, repo, market="ashare")
