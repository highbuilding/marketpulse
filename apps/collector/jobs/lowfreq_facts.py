from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from core.domain.market_calendar import is_trading_day
from core.services.lowfreq_fact_service import LowFreqFactService

log = structlog.get_logger(__name__)
_BJT = ZoneInfo("Asia/Shanghai")


async def refresh_ashare_lowfreq_facts(service: LowFreqFactService) -> None:
    """盘后拉龙虎榜/公告/同花顺低频资金事实。失败只 warning。"""
    now = datetime.now(_BJT)
    if not is_trading_day("ashare", now):
        return
    trade_date = now.date().isoformat()
    try:
        result = await service.pull_all(trade_date)
        log.info("lowfreq.refresh_done", market="ashare", trade_date=trade_date,
                 **result)
    except Exception as e:  # noqa: BLE001
        log.warning("lowfreq.refresh_failed", market="ashare",
                    trade_date=trade_date, error=str(e))
