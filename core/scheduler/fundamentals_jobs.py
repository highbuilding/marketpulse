from __future__ import annotations

import structlog

from core.services.fund_flow_service import FundFlowService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def pull_north_flow_job(svc: FundFlowService) -> None:
    """A 股北向资金, 仅交易日 session 时段拉。"""
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open
    if not is_trading_day("ashare"):
        log.debug("north_flow.skip_non_trading_day")
        return
    if not is_market_session_open("ashare"):
        log.debug("north_flow.skip_off_session")
        return
    try:
        await svc.pull_north_flow()
        log.info("north_flow.pulled")
    except Exception as e:  # noqa: BLE001
        log.warning("north_flow.failed", error=str(e))


async def pull_watchlist_symbol_flow_job(
    ff: FundFlowService, wl: WatchlistService,
) -> None:
    """关注列表 symbol 资金流, 仅 A 股交易 session 时段拉。"""
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open
    if not is_trading_day("ashare"):
        log.debug("symbol_flow.skip_non_trading_day")
        return
    if not is_market_session_open("ashare"):
        log.debug("symbol_flow.skip_off_session")
        return
    symbols = await wl.dynamic_universe()
    pulled = 0
    for s in symbols:
        try:
            pulled += await ff.pull_symbol_flow(s)
        except Exception as e:  # noqa: BLE001
            log.warning("symbol_flow.failed", symbol=s, error=str(e))
    log.info("symbol_flow.batch_done", symbols=len(symbols), rows=pulled)


async def purge_fund_flow_job(ff: FundFlowService) -> None:
    s = await ff.repo.purge_old_symbol(days=30)
    sec = await ff.repo.purge_old_sector(days=90)
    n = await ff.repo.purge_old_north(days=30)
    log.info("fund_flow.purged", symbol=s, sector=sec, north=n)
