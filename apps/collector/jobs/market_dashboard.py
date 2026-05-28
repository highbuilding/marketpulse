"""市场 dashboard 聚合包 — 把"前端 /market 页所需的全部数据"打成一个 cache key。

读取已经被其他 job 预填的 cache (cache:index:*:minute), 组装成
cache:market:ashare:dashboard 给 api 单次返回。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §3.3
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from apps.collector.jobs.index_minute import INDEX_SYMBOLS

log = structlog.get_logger(__name__)

_CACHE_TTL_S = 24 * 3600  # 24h: 收盘后用户看到收盘 dashboard, 不立刻 stale


async def build_dashboard(market: str, *, cache: RedisCache) -> dict:
    """组合多个 cache 片段 → 完整 dashboard。

    本 Plan 范围:仅 indices 这一段先做出来, 后续 section (overview, north_flow,
    hot_sectors) 在前端切换到 dashboard 接口后逐步加 (留 stub)。
    """
    indices = []
    missing = []
    for sym in INDEX_SYMBOLS:
        payload = await cache.get_msgpack(keys.cache_index_minute(sym, days=1))
        if payload is None:
            continue
        indices.append({
            "symbol": payload["symbol"],
            "granularity": payload.get("granularity", "5m"),
            "points": payload.get("points", []),
        })
    if not indices:
        missing.append("indices")

    payload = {
        "market": market,
        "indices": indices,
        "overview": None,    # 留待后续 plan 填
        "north_flow": None,  # 同上
        "hot_sectors": None, # 同上
        "meta": {
            "fresh_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "missing_sections": missing,
        },
    }
    await cache.set_msgpack(keys.cache_market_dashboard(market), payload, ttl_s=_CACHE_TTL_S)
    log.info("dashboard.cached", market=market,
             indices=len(indices), missing=missing)
    return payload


async def refresh_dashboard_job(cache: RedisCache) -> None:
    """APScheduler 调用入口 — 目前只刷 A 股 dashboard。

    非交易日 + 非 session 时段跳过 — 上游 cache (index_minute) 已经
    被各自闸门停了, dashboard 只是聚合 cache, 盘外没有新数据可聚合。
    """
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open
    if not is_trading_day("ashare"):
        log.debug("dashboard.skip_non_trading_day")
        return
    if not is_market_session_open("ashare"):
        log.debug("dashboard.skip_off_session")
        return
    try:
        await build_dashboard("ashare", cache=cache)
    except Exception as e:  # noqa: BLE001
        log.warning("dashboard.refresh_failed", market="ashare", error=str(e))
