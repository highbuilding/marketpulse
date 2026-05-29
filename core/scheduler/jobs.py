from __future__ import annotations

import structlog

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.cache.redis_client import RedisCache
from core.cache import keys as cache_keys
from core.domain.markets import infer_market
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def write_quote_to_redis(q, *, cache: RedisCache, ttl_s: int = 90) -> None:
    """把单条 Quote 写到 Redis cache:quote:{market}:{symbol},供 api 读路径用。

    Plan 1 拆进程后,api 进程的 QuoteCache 是空的;collector 必须把 quote 同时
    落到 Redis 给 api 看。失败仅 warning, 不抛(优雅降级)。
    """
    try:
        payload = {
            "market": q.market,
            "symbol": q.symbol,
            "price": float(q.price),
            "change_pct": q.change_pct,
            "volume": q.volume,
            "ts": q.ts.isoformat(),
        }
        await cache.set_msgpack(
            cache_keys.cache_quote(q.market, q.symbol),
            payload, ttl_s=ttl_s,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("quote.redis_write_failed", symbol=q.symbol, error=str(e))


async def tick_snapshot_once(
    market: str,
    registry: AdapterRegistry,
    cache: QuoteCache,
    watchlist: WatchlistService,
    redis_cache: RedisCache | None = None,
) -> None:
    # 非交易日跳过 — sina/em/yfinance 在节假日返回历史/空数据,无意义打接口
    # 交易日还要落在本市场 session 内 (避免夜里整宿打源)
    # crypto 永远 trading + 24/7 session,自动放行
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open
    if not is_trading_day(market):
        log.debug("tick.skip_non_trading_day", market=market)
        return
    if not is_market_session_open(market):
        log.debug("tick.skip_off_session", market=market)
        return

    adapter = registry.get(market)
    base = set(registry.universe(market)) | set(registry.index_symbols(market))
    # 关注列表里属于本 market 的标的也带上, 让用户加的任意 symbol 都能拿到 quote
    try:
        wl_symbols = await watchlist.dynamic_universe()
        base |= {s for s in wl_symbols if infer_market(s) == market}
    except Exception as e:  # noqa: BLE001
        log.warning("tick.watchlist_load_failed", market=market, error=str(e))
    symbols = list(base)
    if not symbols:
        return
    try:
        quotes = await adapter.fetch_snapshot(symbols)
    except Exception as e:  # noqa: BLE001
        log.warning("tick.failed", market=market, error=str(e))
        return
    for q in quotes:
        cache.put(q)
        if redis_cache is not None:
            await write_quote_to_redis(q, cache=redis_cache)
    log.debug("tick.ok", market=market, count=len(quotes))


def flush_quotes_to_duckdb(
    market: str, cache: QuoteCache, repo: BarRepo,
) -> list[Bar]:
    """同步部分: gate + DuckDB 写. 返回写入的 bars 给上层做 Redis 异步 upsert."""
    from core.domain.market_calendar import is_trading_day
    from core.domain.market_sessions import is_market_session_open
    if not is_trading_day(market):
        return []
    if not is_market_session_open(market):
        return []
    quotes = cache.snapshot(market)
    if not quotes:
        return []
    bars = [
        Bar(
            market=q.market, symbol=q.symbol, ts=q.ts,
            open=q.price, high=q.price, low=q.price, close=q.price,
            volume=q.volume, interval="1m",
        )
        for q in quotes
    ]
    try:
        repo.insert_bars(bars)
    except Exception as e:  # noqa: BLE001
        log.warning("flush.failed", market=market, error=str(e))
        return []
    return bars


def flush_all_quotes_to_duckdb(
    markets: list[str], cache: QuoteCache, repo: BarRepo,
) -> dict[str, list[Bar]]:
    """顺序 flush 所有市场 quote → 1m bar. 返回 {market: bars} 给 async 包装写 Redis."""
    result: dict[str, list[Bar]] = {}
    for market in markets:
        bars = flush_quotes_to_duckdb(market, cache, repo)
        if bars:
            result[market] = bars
    return result


async def flush_all_quotes_to_duckdb_async(
    markets: list[str], cache: QuoteCache, repo: BarRepo,
    redis_bars=None,  # RedisBarsCache | None
) -> None:
    """async 包装: DuckDB 同步写丢进 to_thread, 然后在主 loop 写 Redis tail (api 读路径).
    """
    import asyncio as _asyncio
    from collections import defaultdict
    written = await _asyncio.to_thread(flush_all_quotes_to_duckdb, markets, cache, repo)
    if redis_bars is None:
        return
    for market, bars in written.items():
        by_sym: dict[str, list[Bar]] = defaultdict(list)
        for b in bars:
            by_sym[b.symbol].append(b)
        for sym, group in by_sym.items():
            try:
                await redis_bars.upsert_tail(market, sym, "1m", group)
            except Exception as e:  # noqa: BLE001
                log.warning("flush.redis_write_failed",
                            market=market, symbol=sym, error=str(e))
