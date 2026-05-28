from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.services.market_query import MarketQueryService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/markets", tags=["markets-extras"])
_svc = MarketQueryService()


class RankRowDTO(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float


class TopMeta(BaseModel):
    stale: bool = False
    reason: str | None = None


class TopResponse(BaseModel):
    market: str
    gainers: list[RankRowDTO]
    losers: list[RankRowDTO]
    meta: TopMeta = TopMeta()


@router.get("/{market}/top", response_model=TopResponse)
async def market_top(
    market: str,
    limit: int = Query(10, ge=1, le=50),
) -> TopResponse:
    """A 股/港股涨跌幅榜。

    Plan 3 遗留: 仍通过 MarketQueryService → ak_call 现拉; 失败/超时返回 stale 兜底,
    页面其余部分不受影响。真正的优化(collector 预聚合 cache:market:{m}:top)列入
    docs/TODO.md 待 Plan 4 处理。
    """
    if market not in ("ashare", "hk"):
        raise HTTPException(404, f"top endpoint not supported for market: {market}")

    try:
        if market == "ashare":
            gainers = await _svc.top_ashare("desc", limit)
            losers = await _svc.top_ashare("asc", limit)
        else:
            gainers = await _svc.top_hk("desc", limit)
            losers = await _svc.top_hk("asc", limit)
    except Exception as e:  # noqa: BLE001
        log.warning("market_top.fetch_failed", market=market, error=str(e))
        return TopResponse(
            market=market, gainers=[], losers=[],
            meta=TopMeta(stale=True, reason="upstream_failed"),
        )

    return TopResponse(
        market=market,
        gainers=[RankRowDTO(
            symbol=r.symbol, name=r.name, price=r.price,
            change_pct=r.change_pct, volume=r.volume, amount=r.amount,
        ) for r in gainers],
        losers=[RankRowDTO(
            symbol=r.symbol, name=r.name, price=r.price,
            change_pct=r.change_pct, volume=r.volume, amount=r.amount,
        ) for r in losers],
    )
