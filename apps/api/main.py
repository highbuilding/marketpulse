from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.deps import (
    get_bar_repo, get_fund_flow_service, get_quote_cache, get_registry,
    get_sector_service, get_state_repo, get_watchlist_service,
)
from apps.api.routes import health, market_extras, markets, north_flow, sectors, symbols, watchlists
from apps.api.ws import ticks
from core.scheduler.scheduler import attach_fundamentals_jobs, build_scheduler

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state_repo = get_state_repo()
    await state_repo.init()

    # Plan 2: bootstrap default watchlist
    await get_watchlist_service().bootstrap_default()

    registry = get_registry()
    cache = get_quote_cache()
    bar_repo = get_bar_repo()

    sched = build_scheduler(registry, cache, bar_repo)
    attach_fundamentals_jobs(
        sched,
        fund_flow=get_fund_flow_service(),
        watchlist=get_watchlist_service(),
        sector=get_sector_service(),
    )
    sched.start()
    log.info("app.started", markets=registry.markets())
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        log.info("app.stopped")


app = FastAPI(title="MarketPulse", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(markets.router)
app.include_router(market_extras.router)
app.include_router(symbols.router)
app.include_router(sectors.router)
app.include_router(watchlists.router)
app.include_router(north_flow.router)
app.include_router(ticks.router)
