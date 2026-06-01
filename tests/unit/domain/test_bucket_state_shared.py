from decimal import Decimal
from datetime import datetime, timezone
from core.domain.bucket_state import (
    BucketState, update_bucket, current_bucket, seed_baseline,
)


def test_update_bucket_new_sets_open():
    st = update_bucket(None, Decimal("100"), volume=5)
    assert st.open == st.high == st.low == st.close == Decimal("100")
    assert st.volume == 5


def test_update_bucket_tracks_high_low_close():
    st = update_bucket(None, Decimal("100"), volume=5)
    st = update_bucket(st, Decimal("105"), volume=8)
    st = update_bucket(st, Decimal("98"), volume=12)
    assert st.open == Decimal("100")
    assert st.high == Decimal("105")
    assert st.low == Decimal("98")
    assert st.close == Decimal("98")
    assert st.volume == 12


def test_current_bucket_us_rth_5m():
    # 09:30 ET = 14:30 UTC (EDT 夏令时, 6/1 是 EDT). 5m 桶 09:30-09:35
    now = datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc)
    ob = current_bucket("us", now, 5)
    assert ob is not None
    open_utc, close_utc = ob
    assert open_utc.hour == 14 and open_utc.minute == 30
    assert close_utc.hour == 14 and close_utc.minute == 35


def test_seed_baseline_from_smaller_bars():
    from core.domain.models import Bar
    bars = [
        Bar(market="us", symbol="X", ts=datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc),
            open=Decimal("10"), high=Decimal("12"), low=Decimal("9"), close=Decimal("11"),
            volume=100, interval="5m"),
        Bar(market="us", symbol="X", ts=datetime(2026, 6, 1, 14, 40, tzinfo=timezone.utc),
            open=Decimal("11"), high=Decimal("15"), low=Decimal("10"), close=Decimal("14"),
            volume=200, interval="5m"),
    ]
    st = seed_baseline(bars)
    assert st.open == Decimal("10")
    assert st.high == Decimal("15")
    assert st.low == Decimal("9")
    assert st.close == Decimal("14")
    assert st.volume == 300


def test_seed_baseline_empty_returns_none():
    assert seed_baseline([]) is None
