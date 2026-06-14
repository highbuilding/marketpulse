from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import (
    get_chip_service, get_collector_http_client, get_fund_flow_service,
    get_kline_service, get_redis_cache, get_registry,
    get_symbol_directory_service, get_volume_indicator_service, collector_base_url,
    get_watchlist_service,
)
from core.adapters.registry import AdapterRegistry
from core.cache import keys
from core.domain.intervals import KLINE_INTERVALS
from core.domain.core_symbols import core_symbols
from core.domain.markets import infer_market, normalize_symbol
from core.services.fund_flow_service import FundFlowService
from core.services.chip_service import ChipService
from core.services.kline_service import KLineService
from core.services.symbol_directory_service import SymbolDirectoryService
from core.services.volume_indicator_service import VolumeIndicatorService

router = APIRouter(prefix="/api/symbols", tags=["symbols"])

log = structlog.get_logger(__name__)

_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def _looks_like_us_ticker(q: str) -> bool:
    """1-5 个大写字母, 可选 .X 一字母后缀(如 BRK.B / BF.A)。"""
    return bool(_US_TICKER_RE.match(q.upper()))


class BarDTO(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float | None = None
    turnover: float | None = None
    outstanding_share: float | None = None


class BarsResponseMeta(BaseModel):
    stale: bool = False
    partial: bool = False
    reason: str | None = None


class BarsResponse(BaseModel):
    symbol: str
    interval: str
    bars: list[BarDTO]
    meta: BarsResponseMeta = BarsResponseMeta()


class FundFlowRowDTO(BaseModel):
    ts: str
    main_net: float | None
    super_large_net: float | None
    large_net: float | None
    medium_net: float | None
    small_net: float | None


class FundFlowResponse(BaseModel):
    symbol: str
    rows: list[FundFlowRowDTO]


class ChipSummaryDTO(BaseModel):
    trade_date: str
    profit_ratio: float | None
    avg_cost: float | None
    cost_90_low: float | None
    cost_90_high: float | None
    concentration_90: float | None
    cost_70_low: float | None
    cost_70_high: float | None
    concentration_70: float | None


class ChipSummaryMeta(BaseModel):
    stale: bool = False
    reason: str | None = None


class ChipSummaryResponse(BaseModel):
    symbol: str
    rows: list[ChipSummaryDTO]
    meta: ChipSummaryMeta = ChipSummaryMeta()


class VolumeIndicatorDTO(BaseModel):
    ts: str
    volume: int
    amount: float | None
    turnover: float | None
    vol_ma5: float | None
    vol_ma20: float | None
    amount_ma20: float | None
    volume_ratio: float | None
    single_bar_volume_ratio: float | None
    obv: float
    is_volume_breakout: bool
    is_shrink_pullback: bool


class VolumeIndicatorsResponse(BaseModel):
    symbol: str
    interval: str
    rows: list[VolumeIndicatorDTO]


class ProfileResponse(BaseModel):
    symbol: str
    name: str | None
    market: str | None


class ProfilesResponse(BaseModel):
    profiles: list[ProfileResponse]


class SearchHit(BaseModel):
    symbol: str
    name: str
    market: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=50),
    market: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    core_only: bool = Query(False, description="仅返回采集集(CORE)内标的, 供自选/持仓添加用"),
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
    registry: AdapterRegistry = Depends(get_registry),
) -> SearchResponse:
    hits = await svc.search(q, limit, market=market)
    if core_only:
        # watchlist/持仓 ⊆ 采集集: 只返回能加的标的(∈ 其市场 core_symbols)
        hits = [(s, n, m) for s, n, m in hits if s in set(core_symbols(infer_market(s)))]
    if hits:
        return SearchResponse(query=q, hits=[
            SearchHit(symbol=s, name=n, market=m) for s, n, m in hits
        ])
    # 美股懒加载: market 为 'us' 或未指定, 且 q 像 US ticker → yfinance verify
    # core_only 时跳过懒加载(懒加载的新 ticker 必不在 CORE, 加了也会被拒)
    if not core_only and market in (None, "us") and _looks_like_us_ticker(q):
        try:
            us_adapter = registry.get("us")
            sym = q.upper()
            ok, name = await us_adapter.verify_ticker(sym)
            if ok:
                await svc.upsert_one(sym, name or sym, "us")
                return SearchResponse(query=q, hits=[
                    SearchHit(symbol=sym, name=name or sym, market="us"),
                ])
        except Exception:  # noqa: BLE001
            # 网络 / DB 写失败 → 静默降级到 hits=[]
            pass
    return SearchResponse(query=q, hits=[])




@router.get("/profiles", response_model=ProfilesResponse)
async def profiles(
    symbols: str = Query(..., description="逗号分隔的 symbol 列表"),
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> ProfilesResponse:
    """批量取 profile, 给前端"信号流 / 关注页"消除 N+1 用。
    缺失的 symbol 也返回(name=None), 让前端能整齐渲染。"""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        return ProfilesResponse(profiles=[])
    # 裸 A 股码补后缀, 否则 get_names 查不到 (目录 key 带后缀) + market 误判
    norm = [normalize_symbol(s) for s in syms]
    names = await svc.get_names(norm)
    return ProfilesResponse(profiles=[
        ProfileResponse(symbol=s, name=names.get(s), market=infer_market(s))
        for s in norm
    ])


@router.get("/{symbol}/profile", response_model=ProfileResponse)
async def profile(
    symbol: str,
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
) -> ProfileResponse:
    symbol = normalize_symbol(symbol)  # 裸 A 股码补后缀
    name = await svc.get_name(symbol)
    return ProfileResponse(symbol=symbol, name=name, market=infer_market(symbol))


@router.get("/{symbol}/bars/history", response_model=BarsResponse)
async def bars_history(
    symbol: str,
    interval: str = Query("1d"),
    before: str | None = Query(None, description="ISO ts 游标, 空=最新一页"),
    limit: int = Query(500, ge=1, le=2000),
    client=Depends(get_collector_http_client),
    redis_cache=Depends(get_redis_cache),
) -> BarsResponse:
    """游标分页历史 (币安/TradingView 反向翻页口径)。

    转发到对应市场 collector 的只读接口 (collector 同进程查 DuckDB, 零锁冲突)。
    api 自己绝不碰 DuckDB。collector 不可达 → 优雅降级 stale。
    游标页(before 非空)长 TTL 86400s; 最新页短 TTL 30s。collector 不可达的 stale 不缓存。
    """
    if interval not in KLINE_INTERVALS:
        raise HTTPException(400, f"invalid interval: {interval}")

    market = infer_market(symbol)
    base = collector_base_url(market) if market else None
    if base is None:
        return BarsResponse(
            symbol=symbol, interval=interval, bars=[],
            meta=BarsResponseMeta(stale=True, reason="unknown_market"),
        )

    # --- 查页缓存 ---
    cache_key = keys.cache_barspage(market, symbol, interval, before, limit)
    try:
        cached = await redis_cache._r.get(cache_key)  # noqa: SLF001
        if cached:
            cp = json.loads(cached)
            return BarsResponse(
                symbol=symbol, interval=interval,
                bars=[BarDTO(**b) for b in cp.get("bars", [])],
                meta=BarsResponseMeta(stale=False))
    except Exception:  # noqa: BLE001
        pass

    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if before:
        params["before"] = before
    t0 = time.monotonic()
    try:
        resp = await client.get(f"{base}/internal/bars/history", params=params)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        # 不再静默吞: 区分超时(collector 满载/慢查询)与其他失败, 记录耗时 + 目标,
        # 便于事后定位"为什么 stale"。超时类名含 Timeout(httpx.*TimeoutException 系列)。
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        etype = type(e).__name__
        if "Timeout" in etype:
            log.warning("api.bars_forward_timeout", symbol=symbol, interval=interval,
                        market=market, limit=limit, elapsed_ms=elapsed_ms, base=base)
        else:
            log.warning("api.bars_forward_failed", symbol=symbol, interval=interval,
                        market=market, elapsed_ms=elapsed_ms,
                        error=str(e), error_type=etype)
        return BarsResponse(
            symbol=symbol, interval=interval, bars=[],
            meta=BarsResponseMeta(stale=True, reason="collector_unreachable"),
        )

    raw = payload.get("bars", [])
    meta = payload.get("meta", {})

    # --- 回写页缓存(仅成功转发时写; collector 不可达的 stale 不缓存) ---
    try:
        ttl = 86400 if before else 30  # 游标页(历史不可变)长TTL; 最新页短TTL
        await redis_cache._r.set(  # noqa: SLF001
            cache_key, json.dumps({"bars": raw, "meta": meta}), ex=ttl)
    except Exception:  # noqa: BLE001
        pass

    return BarsResponse(
        symbol=symbol, interval=interval,
        bars=[BarDTO(**b) for b in raw],
        meta=BarsResponseMeta(
            stale=bool(meta.get("stale", False)),
            reason=meta.get("reason"),
        ),
    )


@router.get("/{symbol}/intraday-line")
async def intraday_line(
    symbol: str,
    date: str | None = Query(None, description="ISO 日期, 空=当日"),
    client=Depends(get_collector_http_client),
    redis_cache=Depends(get_redis_cache),
):
    """分时图当日/历史点。转发 collector 内嵌只读接口(api 不碰 DuckDB)。"""
    market = infer_market(symbol)

    # realtime: 美股查 state:us:realtime_active(LRU-30 内才有实时分时); 非美股恒 True
    realtime = True
    if market == "us":
        try:
            realtime = bool(await redis_cache._r.sismember("state:us:realtime_active", symbol))  # noqa: SLF001
        except Exception:  # noqa: BLE001
            realtime = False

    base = collector_base_url(market) if market else None
    if base is None:
        return {"symbol": symbol, "points": [],
                "realtime": realtime,
                "meta": {"stale": True, "reason": "unknown_market"}}
    params = {"symbol": symbol}
    if date:
        params["date"] = date
    try:
        resp = await client.get(f"{base}/internal/intraday-line", params=params)
        resp.raise_for_status()
        body = resp.json()
        body["realtime"] = realtime
        return body
    except Exception as e:  # noqa: BLE001
        log.warning("api.intraday_forward_failed", symbol=symbol, market=market,
                    error=str(e), error_type=type(e).__name__)
        return {"symbol": symbol, "points": [],
                "realtime": realtime,
                "meta": {"stale": True, "reason": "collector_unreachable"}}


@router.get("/{symbol}/bars", response_model=BarsResponse)
async def bars(
    symbol: str,
    interval: str = Query("1d"),
    days: int = Query(365, ge=1, le=3650),
    svc: KLineService = Depends(get_kline_service),
    redis_cache=Depends(get_redis_cache),
    watchlist=Depends(get_watchlist_service),
) -> BarsResponse:
    if interval not in KLINE_INTERVALS:
        raise HTTPException(400, f"invalid interval: {interval}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    bars_list, partial = await svc.get_bars_cache_only(
        symbol, interval=interval, start=start, end=end,
    )
    if not bars_list:
        # 触发 collector 后台 refill (不阻塞)
        try:
            await _publish_refill_request(redis_cache, symbol, interval, days,
                                          watchlist=watchlist)
        except Exception:  # noqa: BLE001
            pass
        return BarsResponse(
            symbol=symbol, interval=interval, bars=[],
            meta=BarsResponseMeta(stale=True, reason="warming_up"),
        )
    return BarsResponse(
        symbol=symbol, interval=interval,
        bars=[BarDTO(
            ts=b.ts.isoformat(),
            open=float(b.open), high=float(b.high),
            low=float(b.low), close=float(b.close),
            volume=b.volume,
            amount=b.amount,
            turnover=b.turnover,
            outstanding_share=b.outstanding_share,
        ) for b in bars_list],
        meta=BarsResponseMeta(partial=partial),
    )


async def _publish_refill_request(redis_cache, symbol: str, interval: str, days: int,
                                  *, watchlist=None) -> None:
    """发 bus:bars.refill_request。白名单(采集清单∪CORE∪watchlist)+ inflight 去重。"""
    import json
    from core.cache import keys as ck
    market = infer_market(symbol) or "unknown"
    # ① 白名单检查:只允许 CORE symbols ∪ watchlist.dynamic_universe()
    allowed = set(core_symbols(market))
    if market == "ashare":
        try:
            from apps.api.deps import get_collector_symbol_repo
            allowed |= set(await get_collector_symbol_repo().active_symbols("ashare", capability="5m"))
        except Exception:  # noqa: BLE001
            pass
    if watchlist is not None:
        try:
            allowed |= set(await watchlist.dynamic_universe())
        except Exception:  # noqa: BLE001
            pass
    if symbol not in allowed:
        return
    # ② inflight 去重:同一 (market, symbol, interval) 60s 内只发一次
    try:
        ok = await redis_cache._r.set(  # noqa: SLF001
            ck.state_inflight(f"refill:{market}:{symbol}:{interval}"),
            b"1", nx=True, ex=60)
        if not ok:
            return
    except Exception:  # noqa: BLE001
        pass
    payload = {"market": market, "symbol": symbol, "interval": interval, "days": days}
    await redis_cache._r.xadd(  # noqa: SLF001
        ck.BUS_BARS_REFILL_REQUEST, {"data": json.dumps(payload)},
        maxlen=100, approximate=True)


@router.get("/{symbol}/chip_summary", response_model=ChipSummaryResponse)
async def chip_summary(
    symbol: str,
    days: int = Query(90, ge=1, le=90),
    svc: ChipService = Depends(get_chip_service),
) -> ChipSummaryResponse:
    if infer_market(symbol) != "ashare":
        return ChipSummaryResponse(
            symbol=symbol, rows=[],
            meta=ChipSummaryMeta(stale=True, reason="non_ashare"),
        )
    rows = await svc.get_summary_cache_only(symbol, days=days)
    if not rows:
        return ChipSummaryResponse(
            symbol=symbol, rows=[],
            meta=ChipSummaryMeta(stale=True, reason="warming_up"),
        )
    return ChipSummaryResponse(
        symbol=symbol,
        rows=[ChipSummaryDTO(
            trade_date=r.trade_date.isoformat(),
            profit_ratio=r.profit_ratio,
            avg_cost=r.avg_cost,
            cost_90_low=r.cost_90_low,
            cost_90_high=r.cost_90_high,
            concentration_90=r.concentration_90,
            cost_70_low=r.cost_70_low,
            cost_70_high=r.cost_70_high,
            concentration_70=r.concentration_70,
        ) for r in rows],
    )


@router.get("/{symbol}/volume_indicators", response_model=VolumeIndicatorsResponse)
async def volume_indicators(
    symbol: str,
    interval: str = Query("1d"),
    days: int = Query(120, ge=1, le=3650),
    kline: KLineService = Depends(get_kline_service),
    svc: VolumeIndicatorService = Depends(get_volume_indicator_service),
    redis_cache=Depends(get_redis_cache),
    watchlist=Depends(get_watchlist_service),
) -> VolumeIndicatorsResponse:
    if interval not in {"1d", "5m", "15m", "30m", "60m"}:
        raise HTTPException(400, "interval must be one of ['1d', '5m', '15m', '30m', '60m']")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    # cache_only — api 进程 BarRepo read_only, 不能触发 adapter+insert_bars.
    # cache miss 时发 refill request 让 collector 后台补, 本次返空 rows.
    bars, _partial = await kline.get_bars_cache_only(
        symbol, interval=interval, start=start, end=end,
    )
    if not bars:
        try:
            await _publish_refill_request(redis_cache, symbol, interval, days,
                                          watchlist=watchlist)
        except Exception:  # noqa: BLE001
            pass
        return VolumeIndicatorsResponse(symbol=symbol, interval=interval, rows=[])
    rows = svc.compute(bars)
    return VolumeIndicatorsResponse(
        symbol=symbol,
        interval=interval,
        rows=[VolumeIndicatorDTO(
            ts=r.ts.isoformat(),
            volume=r.volume,
            amount=r.amount,
            turnover=r.turnover,
            vol_ma5=r.vol_ma5,
            vol_ma20=r.vol_ma20,
            amount_ma20=r.amount_ma20,
            volume_ratio=r.volume_ratio,
            single_bar_volume_ratio=r.single_bar_volume_ratio,
            obv=r.obv,
            is_volume_breakout=r.is_volume_breakout,
            is_shrink_pullback=r.is_shrink_pullback,
        ) for r in rows],
    )


@router.get("/{symbol}/fund_flow", response_model=FundFlowResponse)
async def fund_flow(
    symbol: str,
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> FundFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.query_symbol(symbol, start, end)
    return FundFlowResponse(
        symbol=symbol,
        rows=[FundFlowRowDTO(
            ts=r.ts.isoformat(), main_net=r.main_net,
            super_large_net=r.super_large_net, large_net=r.large_net,
            medium_net=r.medium_net, small_net=r.small_net,
        ) for r in rows],
    )
