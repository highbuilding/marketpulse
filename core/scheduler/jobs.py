from __future__ import annotations

import structlog

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.domain.markets import infer_market
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def tick_snapshot_once(
    market: str,
    registry: AdapterRegistry,
    cache: QuoteCache,
    watchlist: WatchlistService,
) -> None:
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
    log.debug("tick.ok", market=market, count=len(quotes))


def flush_quotes_to_duckdb(market: str, cache: QuoteCache, repo: BarRepo) -> None:
    quotes = cache.snapshot(market)
    if not quotes:
        return
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


def flush_all_quotes_to_duckdb(markets: list[str], cache: QuoteCache, repo: BarRepo) -> None:
    """顺序 flush 所有市场 quote, 避免多个 APScheduler 线程并发写 DuckDB。"""
    for market in markets:
        flush_quotes_to_duckdb(market, cache, repo)
