"""指数分时数据端点 — Plan 3: 全部读 Redis cache,不调外部 API。

A 股指数:collector 的 index_minute job 每 30s 预先把 5min 序列写到
cache:index:{symbol}:minute:1。本路由只 GET cache,无 cache 则返回 stale meta。
港股指数:collector 暂未实装 HK job,统一返回 stale 兜底。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §3.2
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_redis_cache
from core.cache import keys

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/indices", tags=["indices"])


class MinutePoint(BaseModel):
    ts: str
    close: float
    volume: int


class IndexMeta(BaseModel):
    stale: bool = False
    reason: str | None = None
    fresh_at: str | None = None


class IndexMinuteResponse(BaseModel):
    symbol: str
    name: str
    granularity: str  # "5m" 或 "1d"
    points: list[MinutePoint]
    meta: IndexMeta = IndexMeta()


_INDEX_NAME = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000688.SH": "科创50",
    "000016.SH": "上证50",
    "HSI.HK": "恒生指数",
    "HSTECH.HK": "恒生科技指数",
    "HSCEI.HK": "恒生中国企业指数",
}


@router.get("/{symbol}/minute", response_model=IndexMinuteResponse)
async def index_minute(
    symbol: str,
    days: int = Query(1, ge=1, le=30),
    cache=Depends(get_redis_cache),
) -> IndexMinuteResponse:
    if symbol not in _INDEX_NAME:
        raise HTTPException(404, f"unknown index: {symbol}")
    name = _INDEX_NAME[symbol]
    if symbol.endswith(".HK"):
        return await _hk_index_daily(symbol, name, days=days, cache=cache)
    return await _ashare_index_5min(symbol, name, days=days, cache=cache)


async def _ashare_index_5min(symbol: str, name: str, *, days: int, cache) -> IndexMinuteResponse:
    payload = await cache.get_msgpack(keys.cache_index_minute(symbol, days=days))
    if payload is None:
        log.info("indices.minute.cache_miss", symbol=symbol, days=days)
        return IndexMinuteResponse(
            symbol=symbol, name=name, granularity="5m",
            points=[], meta=IndexMeta(stale=True, reason="warming_up"),
        )
    points = [MinutePoint(**p) for p in payload.get("points", [])]
    fresh_at = payload.get("meta", {}).get("fresh_at")
    return IndexMinuteResponse(
        symbol=symbol, name=name,
        granularity=payload.get("granularity", "5m"),
        points=points,
        meta=IndexMeta(stale=False, fresh_at=fresh_at),
    )


async def _hk_index_daily(symbol: str, name: str, *, days: int, cache) -> IndexMinuteResponse:
    """HK 指数 collector job 暂未实装(Plan 4 补)。读 cache 命中则用,否则 stale 兜底。"""
    payload = await cache.get_msgpack(keys.cache_index_minute(symbol, days=days))
    if payload is None:
        return IndexMinuteResponse(
            symbol=symbol, name=name, granularity="1d",
            points=[], meta=IndexMeta(stale=True, reason="hk_index_collector_pending"),
        )
    points = [MinutePoint(**p) for p in payload.get("points", [])]
    return IndexMinuteResponse(
        symbol=symbol, name=name, granularity="1d",
        points=points, meta=IndexMeta(stale=False),
    )
