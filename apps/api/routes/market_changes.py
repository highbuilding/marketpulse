"""盘面异动相关只读接口 — 个股异动 / 板块异动 / 涨停池 / 竞价快照。

api 路由 0 ak_call: changes/board/auction 读 Redis cache (collector job 预写),
limit-pool 读 SQLite (collector 涨停池 cron 落库)。非 A 股一律 stale。

个股 `collected` 标志: 是否在 A 股采集池 collector_symbols active (capability=snapshot)。
口径见 memory project_collector_symbols_sole_universe —— 采集进程唯一事实源, 不掺 CORE/自选。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import (
    get_collector_symbol_repo,
    get_limit_pool_service,
    get_market_changes_service,
    get_redis_cache,
)
from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.market_calendar import is_trading_day
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.services.limit_pool_service import LimitPoolService
from core.services.market_changes_service import MarketChangesService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/markets", tags=["markets-changes"])
_BJT = ZoneInfo("Asia/Shanghai")


class Meta(BaseModel):
    stale: bool = False
    reason: str | None = None
    fresh_at: str | None = None
    trade_date: str | None = None


class ChangeRowDTO(BaseModel):
    time: str
    symbol: str
    name: str | None = None
    change_type: str
    category: str | None = None
    price: float | None = None
    change_pct: float | None = None
    collected: bool = False


class ChangesResponse(BaseModel):
    market: str
    changes: list[ChangeRowDTO] = []
    counts: dict[str, int] = {}
    meta: Meta = Meta()


class BoardRowDTO(BaseModel):
    board_name: str
    change_pct: float | None = None
    main_net_inflow: float | None = None
    change_total: int | None = None
    top_symbol: str | None = None
    top_name: str | None = None
    top_direction: str | None = None


class BoardChangesResponse(BaseModel):
    market: str
    boards: list[BoardRowDTO] = []
    meta: Meta = Meta()


class LimitRowDTO(BaseModel):
    symbol: str
    name: str | None = None
    change_pct: float | None = None
    price: float | None = None
    limit_price: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    seal_amount: float | None = None
    first_seal_time: str | None = None
    last_seal_time: str | None = None
    break_count: int | None = None
    ladder_count: int | None = None
    industry: str | None = None
    collected: bool = False


class LimitPoolResponse(BaseModel):
    market: str
    pool_type: str
    items: list[LimitRowDTO] = []
    meta: Meta = Meta()


class AuctionResponse(BaseModel):
    market: str
    trade_date: str
    # 开盘竞价
    auction_up: list[ChangeRowDTO] = []      # 竞价上涨(抢筹)
    auction_down: list[ChangeRowDTO] = []    # 竞价下跌(砸盘)
    one_word_limit: list[LimitRowDTO] = []   # 一字涨停(竞价即封)
    prev_limit_today: list[LimitRowDTO] = []  # 昨涨停今表现
    # 尾盘 (14:55 后)
    late_surge: list[ChangeRowDTO] = []      # 尾盘拉升
    late_plunge: list[ChangeRowDTO] = []     # 尾盘跳水
    late_seal: list[ChangeRowDTO] = []       # 尾盘封板(抢板)
    late_broken: list[ChangeRowDTO] = []     # 尾盘炸板
    meta: Meta = Meta()


def _change_dto(it, collected: set[str]) -> ChangeRowDTO:
    from core.services.market_changes_service import CHANGE_CATEGORY
    return ChangeRowDTO(
        time=it.change_time, symbol=it.symbol, name=it.name,
        change_type=it.change_type, category=CHANGE_CATEGORY.get(it.change_type),
        price=it.price, change_pct=it.change_pct, collected=it.symbol in collected,
    )


def _limit_dto(it, collected: set[str]) -> LimitRowDTO:
    return LimitRowDTO(
        symbol=it.symbol, name=it.name, change_pct=it.change_pct, price=it.price,
        limit_price=it.limit_price, amount=it.amount, turnover_rate=it.turnover_rate,
        seal_amount=it.seal_amount, first_seal_time=it.first_seal_time,
        last_seal_time=it.last_seal_time, break_count=it.break_count,
        ladder_count=it.ladder_count, industry=it.industry,
        collected=it.symbol in collected,
    )


async def _collected_set(repo: CollectorSymbolRepo) -> set[str]:
    try:
        return set(await repo.active_symbols("ashare", capability="snapshot"))
    except Exception as e:  # noqa: BLE001
        log.warning("market_changes.collected_set_failed", error=str(e))
        return set()


def _latest_trade_date(now: datetime | None = None) -> str:
    """最近一个 A 股交易日 (含今天)。off-session/周末回退到最近交易日。"""
    d = (now or datetime.now(_BJT)).date()
    for _ in range(10):
        if is_trading_day("ashare", datetime.combine(d, datetime.min.time(), _BJT)):
            return d.isoformat()
        d -= timedelta(days=1)
    return d.isoformat()


@router.get("/{market}/changes", response_model=ChangesResponse)
async def market_changes(
    market: str,
    types: str | None = Query(None, description="逗号分隔的 category 过滤"),
    limit: int = Query(200, ge=1, le=500),
    cache: RedisCache = Depends(get_redis_cache),
    symbol_repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
) -> ChangesResponse:
    if market != "ashare":
        return ChangesResponse(market=market, meta=Meta(stale=True, reason="ashare_only"))
    payload = await cache.get_msgpack(keys.cache_market_changes(market))
    if payload is None:
        return ChangesResponse(market=market, meta=Meta(stale=True, reason="warming_up"))
    collected = await _collected_set(symbol_repo)
    cat_filter = {t.strip() for t in types.split(",")} if types else None
    rows: list[ChangeRowDTO] = []
    for r in payload.get("changes", []):
        if cat_filter and r.get("category") not in cat_filter:
            continue
        rows.append(ChangeRowDTO(collected=r.get("symbol") in collected, **r))
        if len(rows) >= limit:
            break
    meta = payload.get("meta", {})
    return ChangesResponse(
        market=market, changes=rows, counts=payload.get("counts", {}),
        meta=Meta(stale=False, fresh_at=meta.get("fresh_at"),
                  trade_date=meta.get("trade_date")),
    )


@router.get("/{market}/board-changes", response_model=BoardChangesResponse)
async def market_board_changes(
    market: str,
    limit: int = Query(50, ge=1, le=100),
    cache: RedisCache = Depends(get_redis_cache),
) -> BoardChangesResponse:
    if market != "ashare":
        return BoardChangesResponse(market=market, meta=Meta(stale=True, reason="ashare_only"))
    payload = await cache.get_msgpack(keys.cache_market_board_changes(market))
    if payload is None:
        return BoardChangesResponse(market=market, meta=Meta(stale=True, reason="warming_up"))
    boards = [BoardRowDTO(**b) for b in payload.get("boards", [])][:limit]
    meta = payload.get("meta", {})
    return BoardChangesResponse(
        market=market, boards=boards,
        meta=Meta(stale=False, fresh_at=meta.get("fresh_at"),
                  trade_date=meta.get("trade_date")),
    )


@router.get("/{market}/limit-pool", response_model=LimitPoolResponse)
async def market_limit_pool(
    market: str,
    pool_type: str = Query("limit_up",
                           pattern="^(limit_up|broken_limit|down_limit|previous)$"),
    date: str | None = None,
    service: LimitPoolService = Depends(get_limit_pool_service),
    symbol_repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
) -> LimitPoolResponse:
    if market != "ashare":
        return LimitPoolResponse(market=market, pool_type=pool_type,
                                 meta=Meta(stale=True, reason="ashare_only"))
    trade_date = date or _latest_trade_date()
    items = await service.list_by_date(trade_date, pool_type=pool_type, market=market)
    if not items:
        return LimitPoolResponse(market=market, pool_type=pool_type,
                                 meta=Meta(stale=True, reason="no_data",
                                           trade_date=trade_date))
    collected = await _collected_set(symbol_repo)
    rows = [
        LimitRowDTO(
            symbol=it.symbol, name=it.name, change_pct=it.change_pct, price=it.price,
            limit_price=it.limit_price, amount=it.amount, turnover_rate=it.turnover_rate,
            seal_amount=it.seal_amount, first_seal_time=it.first_seal_time,
            last_seal_time=it.last_seal_time, break_count=it.break_count,
            ladder_count=it.ladder_count, industry=it.industry,
            collected=it.symbol in collected,
        )
        for it in items
    ]
    return LimitPoolResponse(market=market, pool_type=pool_type, items=rows,
                             meta=Meta(stale=False, trade_date=trade_date))


@router.get("/{market}/auction", response_model=AuctionResponse)
async def market_auction(
    market: str,
    date: str | None = None,
    limit: int = Query(30, ge=1, le=100),
    changes_svc: MarketChangesService = Depends(get_market_changes_service),
    limit_svc: LimitPoolService = Depends(get_limit_pool_service),
    symbol_repo: CollectorSymbolRepo = Depends(get_collector_symbol_repo),
) -> AuctionResponse:
    """竞价快照(读时组合): 开盘竞价异动/一字板/昨涨停今表现 + 尾盘抢板炸板拉升跳水。

    全部由已采集的 SQLite 事实表组合, 不触发 ak_call。竞价 tape 逐分钟数据源不可得,
    本端点是"竞价结果快照"口径。
    """
    if market != "ashare":
        return AuctionResponse(market=market, trade_date=date or _latest_trade_date(),
                               meta=Meta(stale=True, reason="ashare_only"))
    trade_date = date or _latest_trade_date()
    collected = await _collected_set(symbol_repo)

    # 开盘竞价异动 (竞价上涨/下跌, 全天留存 SQLite)
    auction_up = await changes_svc.list_changes(
        market, trade_date, change_types=["竞价上涨"], limit=limit)
    auction_down = await changes_svc.list_changes(
        market, trade_date, change_types=["竞价下跌"], limit=limit)

    # 一字板 + 昨涨停今表现 (涨停池)
    limit_up = await limit_svc.list_by_date(trade_date, pool_type="limit_up", market=market)
    one_word = [it for it in limit_up
                if it.first_seal_time and it.first_seal_time <= "093100"]
    prev = await limit_svc.list_by_date(trade_date, pool_type="previous", market=market)

    # 尾盘 (14:55 后的异动, 按 category 分桶)
    late = await changes_svc.list_changes(
        market, trade_date,
        change_types=["火箭发射", "快速反弹", "加速下跌", "高台跳水",
                      "封涨停板", "打开涨停板"],
        limit=500)
    late = [c for c in late if c.change_time >= "14:55:00"]
    surge_types = {"火箭发射", "快速反弹"}
    plunge_types = {"加速下跌", "高台跳水"}
    late_surge = [c for c in late if c.change_type in surge_types][:limit]
    late_plunge = [c for c in late if c.change_type in plunge_types][:limit]
    late_seal = [c for c in late if c.change_type == "封涨停板"][:limit]
    late_broken = [c for c in late if c.change_type == "打开涨停板"][:limit]

    has_any = any([auction_up, auction_down, one_word, prev, late])
    return AuctionResponse(
        market=market, trade_date=trade_date,
        auction_up=[_change_dto(c, collected) for c in auction_up],
        auction_down=[_change_dto(c, collected) for c in auction_down],
        one_word_limit=[_limit_dto(it, collected) for it in one_word][:limit],
        prev_limit_today=[_limit_dto(it, collected) for it in prev][:limit],
        late_surge=[_change_dto(c, collected) for c in late_surge],
        late_plunge=[_change_dto(c, collected) for c in late_plunge],
        late_seal=[_change_dto(c, collected) for c in late_seal],
        late_broken=[_change_dto(c, collected) for c in late_broken],
        meta=Meta(stale=not has_any, reason=None if has_any else "no_data",
                  trade_date=trade_date),
    )
