"""AI 市场数据包预聚合 — Plan 3 Hotfix C 引入。

替代 /api/ai/ashare/market-packet 路由内的 build_ashare_packet 调用(链路上多次 ak_call,
极易超时阻塞)。

collector 的 ai_packet job 每 60s 调一次 build, 写到 cache:market:ashare:ai_packet。
api 路由直读 cache,miss 时返回 stale meta 兜底。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.services.ai_market_service import AIMarketService, AIPacket

log = structlog.get_logger(__name__)

_CACHE_TTL_S = 240  # 60s 写 + 240s TTL = 容忍偶发失败 + ak 限流


def packet_to_dict(packet: AIPacket) -> dict:
    """把 AIPacket 序列化成 JSON-ready dict, 与 /api/ai/ashare/market-packet
    response 结构一致, msgpack 友好。
    """
    return {
        "generated_at": packet.generated_at.isoformat(),
        "market": packet.market,
        "indices": [asdict(row) for row in packet.indices],
        "breadth": asdict(packet.breadth),
        "top_gainers": [asdict(row) for row in packet.top_gainers],
        "top_losers": [asdict(row) for row in packet.top_losers],
        "hot_sectors": [asdict(row) for row in packet.hot_sectors],
        "weak_sectors": [asdict(row) for row in packet.weak_sectors],
        "watchlist": [asdict(row) for row in packet.watchlist],
        "index_strength": asdict(packet.index_strength),
        "events": [asdict(row) for row in packet.events],
        "ai_brief": packet.ai_brief,
        "degraded": packet.degraded,
    }


async def refresh_ai_packet(
    *, svc: AIMarketService, cache: RedisCache,
) -> None:
    """拉一次 AI 大盘数据并写 cache。失败仅 warning, 不抛。

    A 股非交易日 + 非 session 时段跳过 (链路上 spot_em + sector_em 多次调用浪费)。
    """
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open

    if not is_trading_day("ashare"):
        log.debug("ai_packet.skip_non_trading_day")
        return
    if not is_market_session_open("ashare"):
        log.debug("ai_packet.skip_off_session")
        return

    try:
        packet = await svc.build_ashare_packet()
    except Exception as e:  # noqa: BLE001
        log.warning("ai_packet.build_failed", error=str(e))
        return

    payload = packet_to_dict(packet)
    payload["meta"] = {
        "fresh_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
    }
    await cache.set_msgpack(
        keys.cache_market_ai_packet("ashare"), payload, ttl_s=_CACHE_TTL_S,
    )
    log.info("ai_packet.cached",
             gainers=len(payload["top_gainers"]),
             losers=len(payload["top_losers"]),
             hot_sectors=len(payload["hot_sectors"]),
             degraded=payload["degraded"])
