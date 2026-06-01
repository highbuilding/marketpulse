from datetime import datetime, timezone
from apps.collector.us.trade_hub import TradeAccumulator


def _utc(h, m):
    return datetime(2026, 6, 1, h, m, tzinfo=timezone.utc)


def test_accumulates_vwap():
    acc = TradeAccumulator()
    acc.add_trade(price=100.0, size=10, ts=_utc(14, 0))   # 10:00 ET RTH
    acc.add_trade(price=110.0, size=20, ts=_utc(14, 1))
    assert acc.cum_volume == 30
    assert acc.cum_amount == 3200.0
    assert abs(acc.vwap() - 3200.0 / 30) < 1e-9
    assert acc.last_price == 110.0


def test_vwap_zero_volume_falls_back_to_last_price():
    acc = TradeAccumulator()
    assert acc.vwap() == 0.0


def test_resets_on_new_et_day():
    acc = TradeAccumulator()
    acc.add_trade(price=100.0, size=10, ts=_utc(14, 0))      # 6/1 RTH
    acc.add_trade(price=200.0, size=5, ts=datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc))  # 6/2 RTH
    assert acc.cum_volume == 5
    assert acc.cum_amount == 1000.0
    assert acc.session_date.day == 2
