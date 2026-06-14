import pytest
import fakeredis.aioredis
from datetime import datetime, timezone
import json

from core.cache import keys
from core.cache.quote_cache import QuoteCache
from core.cache.redis_client import RedisCache
from core.domain.models import CollectorSymbol, Quote
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.sqlite_repo import StateRepo
from core.scheduler.jobs import tick_snapshot_once, write_quote_to_redis


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_write_quote_to_redis_msgpack_payload(cache):
    q = Quote(market="ashare", symbol="600519.SH", price=1234.5,
              change_pct=1.2, volume=100,
              ts=datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc),
              source="test")
    await write_quote_to_redis(q, cache=cache)
    payload = await cache.get_msgpack(keys.cache_quote("ashare", "600519.SH"))
    assert payload is not None
    assert payload["symbol"] == "600519.SH"
    assert payload["price"] == 1234.5
    assert payload["change_pct"] == 1.2
    assert payload["volume"] == 100
    assert payload["ts"] == "2026-05-27T01:00:00+00:00"
    assert payload["market"] == "ashare"

    entries = await cache._r.xrange(keys.BUS_QUOTE_TICK)  # noqa: SLF001
    assert len(entries) == 1
    raw = entries[0][1][b"data"]
    event = json.loads(raw)
    assert event["market"] == "ashare"
    assert event["symbol"] == "600519.SH"
    assert event["amount"] is None


async def test_write_quote_to_redis_swallows_errors(cache, monkeypatch):
    async def raise_set(*args, **kwargs):
        raise RuntimeError("redis down")
    monkeypatch.setattr(cache, "set_msgpack", raise_set)
    q = Quote(market="ashare", symbol="X.SH", price=1.0, change_pct=0.0,
              volume=0, ts=datetime.now(timezone.utc), source="test")
    # 不应抛
    await write_quote_to_redis(q, cache=cache)


@pytest.mark.asyncio
async def test_tick_snapshot_reads_enabled_collector_symbols(tmp_path, monkeypatch):
    await StateRepo(str(tmp_path / "state.db")).init()
    collector_repo = CollectorSymbolRepo(str(tmp_path / "state.db"))
    await collector_repo.seed_symbols([
        CollectorSymbol(
            market="ashare",
            symbol="300001.SZ",
            name="测试成分",
            source="manual",
            collect_snapshot=True,
            collect_5m=True,
            collect_signals=True,
        ),
    ])
    monkeypatch.setattr("core.domain.market_calendar.is_trading_day", lambda _market: True)
    monkeypatch.setattr("core.domain.market_sessions.is_market_session_open", lambda _market: True)

    class Adapter:
        def __init__(self):
            self.symbols: list[str] = []

        async def fetch_snapshot(self, symbols):
            self.symbols = symbols
            return [
                Quote(
                    market="ashare",
                    symbol="300001.SZ",
                    price=10,
                    change_pct=1.0,
                    volume=100,
                    ts=datetime(2026, 6, 15, 1, 30, tzinfo=timezone.utc),
                    source="test",
                ),
            ]

    adapter = Adapter()

    class Registry:
        def get(self, _market):
            return adapter

        def universe(self, _market):
            return []

        def index_symbols(self, _market):
            return []

    class Watchlist:
        async def dynamic_universe(self):
            return []

    watchlist = Watchlist()
    cache = QuoteCache()

    await tick_snapshot_once(
        "ashare",
        Registry(),
        cache,
        watchlist,
        collector_symbols=collector_repo,
    )

    assert "300001.SZ" in adapter.symbols
    assert cache.get("ashare", "300001.SZ") is not None
