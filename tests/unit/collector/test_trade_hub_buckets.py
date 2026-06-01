from datetime import datetime, timezone
from unittest.mock import MagicMock
from apps.collector.us.trade_hub import TradeHub


def _utc(h, m, s=0):
    return datetime(2026, 6, 1, h, m, s, tzinfo=timezone.utc)


def test_on_trade_builds_current_bucket():
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=MagicMock(), ticker=MagicMock())
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10, ts=_utc(14, 31))  # 10:31 ET, 桶 14:30-14:35
    tr = hub._buckets[("AAPL", "5m")]
    assert tr.open_ts == _utc(14, 30)
    assert float(tr.state.close) == 100.0
    assert tr.state.volume == 10
    assert "AAPL" in hub._dirty


def test_on_trade_accumulates_volume_same_bucket():
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=MagicMock(), ticker=MagicMock())
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10, ts=_utc(14, 31))
    hub.on_trade("AAPL", price=105.0, size=20, ts=_utc(14, 32))
    tr = hub._buckets[("AAPL", "5m")]
    assert tr.state.volume == 30  # 累加
    assert float(tr.state.high) == 105.0
    assert float(tr.state.close) == 105.0


def test_on_trade_roll_marks_just_closed():
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=MagicMock(), ticker=MagicMock())
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10, ts=_utc(14, 31))   # 桶 14:30-14:35
    hub.on_trade("AAPL", price=106.0, size=5, ts=_utc(14, 36))    # 滚到 14:35-14:40
    assert ("AAPL", "5m") in hub._just_closed
    closed = hub._just_closed[("AAPL", "5m")]
    assert closed.open_ts == _utc(14, 30)
    assert hub._buckets[("AAPL", "5m")].open_ts == _utc(14, 35)
