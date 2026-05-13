from __future__ import annotations

import structlog

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)


async def tick_snapshot_once(market: str, registry: AdapterRegistry, cache: QuoteCache) -> None:
    adapter = registry.get(market)
    symbols = registry.universe(market) + registry.index_symbols(market)
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
