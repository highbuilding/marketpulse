from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from core.domain.market_calendar import is_trading_day
from core.services.watch_candidate_service import WatchCandidateService

log = structlog.get_logger(__name__)
_BJT = ZoneInfo("Asia/Shanghai")


async def refresh_ashare_watch_candidates(
    service: WatchCandidateService,
) -> None:
    """收盘后生成低位容量趋势观察池。失败只 warning, 不影响复盘/主行情。"""
    now = datetime.now(_BJT)
    if not is_trading_day("ashare", now):
        return
    trade_date = now.date().isoformat()
    try:
        rows = await service.generate_low_position_capacity_trend(
            "ashare", trade_date=trade_date, limit=50)
        log.info("watch_candidates.refresh_done", market="ashare",
                 trade_date=trade_date, count=len(rows))
    except Exception as e:  # noqa: BLE001
        log.warning("watch_candidates.refresh_failed", market="ashare",
                    trade_date=trade_date, error=str(e))
