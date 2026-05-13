import time
from datetime import datetime, timezone
from decimal import Decimal

from core.cache.quote_cache import QuoteCache
from core.domain.models import Quote


def _q(market="ashare", symbol="X", price="1"):
    return Quote(
        market=market, symbol=symbol,
        ts=datetime.now(timezone.utc),
        price=Decimal(price), change_pct=0, volume=0, source="t",
    )


def test_put_and_get():
    c = QuoteCache(ttl_s=60)
    q = _q(symbol="000858.SZ")
    c.put(q)
    assert c.get("ashare", "000858.SZ") is q


def test_get_returns_none_when_expired():
    c = QuoteCache(ttl_s=0.01)
    c.put(_q(symbol="A"))
    time.sleep(0.02)
    assert c.get("ashare", "A") is None


def test_snapshot_returns_all_fresh():
    c = QuoteCache(ttl_s=60)
    c.put(_q(symbol="A"))
    c.put(_q(symbol="B"))
    snap = c.snapshot("ashare")
    assert {q.symbol for q in snap} == {"A", "B"}


def test_snapshot_filters_expired():
    c = QuoteCache(ttl_s=0.01)
    c.put(_q(symbol="A"))
    time.sleep(0.02)
    c.put(_q(symbol="B"))
    snap = c.snapshot("ashare")
    assert {q.symbol for q in snap} == {"B"}
