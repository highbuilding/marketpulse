from __future__ import annotations

import structlog

from core.services.fund_flow_service import FundFlowService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def pull_north_flow_job(svc: FundFlowService) -> None:
    try:
        await svc.pull_north_flow()
        log.info("north_flow.pulled")
    except Exception as e:  # noqa: BLE001
        log.warning("north_flow.failed", error=str(e))


async def pull_watchlist_symbol_flow_job(
    ff: FundFlowService, wl: WatchlistService,
) -> None:
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
