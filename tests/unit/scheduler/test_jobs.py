from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.cache.quote_cache import QuoteCache
from core.domain.models import Quote
from core.scheduler.jobs import flush_quotes_to_duckdb, tick_snapshot_once


def _q(market, symbol, price):
    return Quote(
        market=market, symbol=symbol, ts=datetime.now(timezone.utc),
        price=Decimal(price), change_pct=0, volume=100, source="t",
    )


@pytest.mark.asyncio
async def test_tick_snapshot_fills_cache():
    cache = QuoteCache(ttl_s=60)
    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(return_value=[_q("ashare", "A", "1"), _q("ashare", "B", "2")])
    registry = MagicMock()
    registry.get.return_value = adapter
    registry.universe.return_value = ["A", "B"]
    registry.index_symbols.return_value = []

    await tick_snapshot_once("ashare", registry, cache)
    assert {q.symbol for q in cache.snapshot("ashare")} == {"A", "B"}


@pytest.mark.asyncio
async def test_tick_handles_adapter_error():
    cache = QuoteCache(ttl_s=60)
    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(side_effect=RuntimeError("boom"))
    registry = MagicMock()
    registry.get.return_value = adapter
    registry.universe.return_value = ["A"]
    registry.index_symbols.return_value = []

    await tick_snapshot_once("ashare", registry, cache)
    assert cache.snapshot("ashare") == []


def test_flush_quotes_converts_to_bars_and_writes():
    cache = QuoteCache(ttl_s=60)
    cache.put(_q("ashare", "A", "1.5"))
    repo = MagicMock()
    flush_quotes_to_duckdb("ashare", cache, repo)
    assert repo.insert_bars.called
    bars = repo.insert_bars.call_args[0][0]
    assert len(bars) == 1
    assert bars[0].symbol == "A"
    assert bars[0].interval == "1m"
