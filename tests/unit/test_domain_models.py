from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.domain.models import Bar, Fundamental, HealthStatus, Quote


def test_quote_basic_fields():
    q = Quote(
        market="ashare",
        symbol="000858.SZ",
        ts=datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc),
        price=Decimal("180.50"),
        change_pct=1.25,
        volume=12000,
        source="akshare",
    )
    assert q.market == "ashare"
    assert q.symbol == "000858.SZ"
    assert q.price == Decimal("180.50")


def test_bar_open_high_low_close():
    b = Bar(
        market="us",
        symbol="AAPL",
        ts=datetime(2026, 5, 13, tzinfo=timezone.utc),
        open=Decimal("190"),
        high=Decimal("192"),
        low=Decimal("189"),
        close=Decimal("191.5"),
        volume=1_000_000,
        interval="1d",
    )
    assert b.high >= b.low
    assert b.interval == "1d"


def test_fundamental_optional_fields():
    f = Fundamental(symbol="AAPL", pe_ttm=28.5, pb=42.0, ev_ebitda=20.1)
    assert f.pe_ttm == 28.5
    assert f.market_cap is None


def test_health_status_states():
    h = HealthStatus(name="ashare", state="ok", detail=None)
    assert h.is_ok()
    assert HealthStatus(name="us", state="disabled", detail="missing key").is_ok() is False


def test_quote_rejects_negative_price():
    with pytest.raises(ValueError):
        Quote(
            market="us", symbol="X", ts=datetime.now(timezone.utc),
            price=Decimal("-1"), change_pct=0, volume=0, source="test",
        )
