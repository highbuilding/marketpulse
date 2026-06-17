from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from core.domain.market_calendar import is_trading_day
from core.services.limit_pool_service import LimitPoolService

log = structlog.get_logger(__name__)
_BJT = ZoneInfo("Asia/Shanghai")


async def refresh_ashare_limit_pool(service: LimitPoolService) -> None:
    """低频刷新 A 股涨停/炸板/跌停池。

    东财股池接口是结论层增强数据,不能影响主行情采集。任何失败只 warning。
    """
    now = datetime.now(_BJT)
    if not is_trading_day("ashare", now):
        return
    trade_date = now.date().isoformat()
    try:
        result = await service.pull_all(trade_date)
        log.info("limit_pool.refresh_done", market="ashare", trade_date=trade_date, **result)
    except Exception as e:  # noqa: BLE001
        log.warning("limit_pool.refresh_failed", market="ashare", trade_date=trade_date, error=str(e))
