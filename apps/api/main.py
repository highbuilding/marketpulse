from __future__ import annotations

from contextlib import asynccontextmanager

# 必须在 import adapters 之前 load .env, 否则 USAdapter().__init__ 拿不到 ALPACA_*
from dotenv import load_dotenv
load_dotenv()

# 必须在 import adapters 之前: 代理 env 在 requests/httpx/yfinance/Alpaca SDK 实例化前就位
from core.integrations.proxy_setup import setup_process_proxy
setup_process_proxy()

# 标记 api 进程 BarRepo 为 read-only, 避免与 collector 争 DuckDB 写锁
import os as _os
_os.environ["MARKETPULSE_BARREPO_READONLY"] = "1"

# 必须在其他 import 之前装好日志, 让 startup 期任何错误也能落到 data/logs/
from core.integrations.logging_setup import setup_logging
setup_logging(process_name="api")

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.deps import get_state_repo
from apps.api.auth import AuthMiddleware, router as auth_router
from apps.api.routes import (
    ai_market, cd_signals, dashboard, health, indices, market_extras, north_flow,
    notifications, sse_bars, sse_intraday, sse_signals, symbols, watchlists,
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

    # SSE hub: 每 worker 一个读流任务, 解析一次按 symbol 分发(替代每连接各自 xread)
    import asyncio
    from apps.api.sse_hub import StreamHub, bars_key, intraday_key
    from core.cache import keys as _keys
    _rc = get_redis_cache()
    app.state.bars_hub = StreamHub(_rc, _keys.BUS_BARS_UPDATED, bars_key)
    app.state.intraday_hub = StreamHub(_rc, _keys.BUS_INTRADAY_UPDATED, intraday_key)
    app.state.signal_hub = StreamHub(_rc, _keys.BUS_SIGNAL_NEW, lambda _m: "*")
    _hub_tasks = [
        asyncio.create_task(app.state.bars_hub.run(), name="sse_bars_hub"),
        asyncio.create_task(app.state.intraday_hub.run(), name="sse_intraday_hub"),
        asyncio.create_task(app.state.signal_hub.run(), name="sse_signal_hub"),
    ]
    log.info("sse_hubs.started")

    log.info("api.started")
    yield
    app.state.bars_hub.stop()
    app.state.intraday_hub.stop()
    app.state.signal_hub.stop()
    for _t in _hub_tasks:
        _t.cancel()
    await asyncio.gather(*_hub_tasks, return_exceptions=True)
    log.info("api.stopped")


app = FastAPI(title="MarketPulse", lifespan=lifespan)
# AuthMiddleware 先加 → CORS 后加 → Starlette LIFO: CORS 最外层处理
# 确保 OPTIONS 预检 + 401 响应都带 CORS 头
app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(auth_router)
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
app.include_router(sse_bars.router)
app.include_router(sse_signals.router)
app.include_router(sse_intraday.router)
app.include_router(ticks.router)
