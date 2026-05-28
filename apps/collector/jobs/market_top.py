"""市场 top 涨跌幅榜预拉取 — Plan 3 后续 Hotfix A 引入。

替代 apps/api/routes/market_extras.py 的"路由内 ak_call",前端 /market 页 top 卡片
读 cache 不阻塞。

A 股调用 stock_zh_a_spot_em (5000 标的, 8s 超时易触发); 港股调用 stock_hk_spot_em。
分别每 60s 一次, 失败仅 warning, 不影响下一轮。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §3.3
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.services.market_query import MarketQueryService

log = structlog.get_logger(__name__)

_CACHE_TTL_S = 180  # 60s 写 + 180s TTL = 容许偶发 ak 失败


async def refresh_market_top(
    market: str, *, svc: MarketQueryService, cache: RedisCache, limit: int = 10,
) -> None:
    """拉一次涨跌榜并写 cache。失败仅 warning, 不抛。

    非交易日 + 非 session 时段跳过 — 避免节假日/夜里打 em 接口浪费配额。
    """
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open

    if not is_trading_day(market):
        log.debug("market_top.skip_non_trading_day", market=market)
        return
    if not is_market_session_open(market):
        log.debug("market_top.skip_off_session", market=market)
        return

    try:
        if market == "ashare":
            gainers = await svc.top_ashare("desc", limit)
            losers = await svc.top_ashare("asc", limit)
        elif market == "hk":
            gainers = await svc.top_hk("desc", limit)
            losers = await svc.top_hk("asc", limit)
        else:
            log.warning("market_top.unsupported_market", market=market)
            return
    except Exception as e:  # noqa: BLE001
        log.warning("market_top.fetch_failed", market=market, error=str(e))
        return

    payload = {
        "market": market,
        "gainers": [asdict(r) for r in gainers],
        "losers": [asdict(r) for r in losers],
        "meta": {
            "fresh_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "limit": limit,
        },
    }
    await cache.set_msgpack(keys.cache_market_top(market), payload, ttl_s=_CACHE_TTL_S)
    log.info("market_top.cached", market=market,
             gainers=len(gainers), losers=len(losers))


async def refresh_all_top_jobs(svc: MarketQueryService, cache: RedisCache) -> None:
    """A 股 + 港股一次刷。两个市场独立, 一边失败不影响另一边。"""
    for market in ("ashare", "hk"):
        await refresh_market_top(market, svc=svc, cache=cache)
