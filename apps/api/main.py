from __future__ import annotations

from contextlib import asynccontextmanager

# 必须在 import adapters 之前 load .env, 否则 USAdapter().__init__ 拿不到 ALPACA_*
from dotenv import load_dotenv
load_dotenv()

# 必须在其他 import 之前装好日志, 让 startup 期任何错误也能落到 data/logs/
from core.integrations.logging_setup import setup_logging
setup_logging()

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.deps import get_state_repo
from apps.api.routes import (
    ai_market, cd_signals, dashboard, health, indices, market_extras, north_flow,
    notifications, symbols, watchlists,
)
from apps.api.ws import ticks

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Plan 1: ping Redis,失败仅 warning 不阻塞 (优雅降级)
    from apps.api.deps import get_redis_cache
    redis_ok = await get_redis_cache().ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="api 将退化到 DB 直读模式,直到 Redis 恢复")

    state_repo = get_state_repo()
    await state_repo.init()

    log.info("api.started")
    yield
    log.info("api.stopped")


app = FastAPI(title="MarketPulse", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ai_market.router)
app.include_router(market_extras.router)
app.include_router(dashboard.router)
app.include_router(symbols.router)
app.include_router(watchlists.router)
app.include_router(north_flow.router)
app.include_router(indices.router)
app.include_router(cd_signals.router)
app.include_router(notifications.router)
app.include_router(ticks.router)
