from datetime import timezone
from apps.collector.us.ws_consumer import _parse_trade


def test_parse_trade_extracts_price_size_ts():
    msg = {"T": "t", "S": "AAPL", "p": 150.25, "s": 100,
           "t": "2026-06-01T14:30:00.123Z"}
    tr = _parse_trade(msg)
    assert tr is not None
    symbol, price, size, ts = tr
    assert symbol == "AAPL"
    assert price == 150.25
    assert size == 100
    assert ts.tzinfo == timezone.utc and ts.hour == 14 and ts.minute == 30


def test_parse_trade_missing_symbol_returns_none():
    assert _parse_trade({"T": "t", "p": 1.0, "s": 1, "t": "2026-06-01T14:30:00Z"}) is None
