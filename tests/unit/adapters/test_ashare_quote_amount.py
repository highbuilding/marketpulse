from decimal import Decimal
from core.domain.models import Quote
from datetime import datetime, timezone


def test_quote_has_amount_field():
    q = Quote(market="ashare", symbol="600519.SH",
              ts=datetime.now(timezone.utc), price=Decimal("1700"),
              change_pct=1.0, volume=1000, source="sina", amount=1700000.0)
    assert q.amount == 1700000.0


def test_quote_amount_defaults_none():
    q = Quote(market="ashare", symbol="600519.SH",
              ts=datetime.now(timezone.utc), price=Decimal("1700"),
              change_pct=1.0, volume=1000, source="sina")
    assert q.amount is None
