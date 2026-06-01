from datetime import datetime, timezone
from core.domain.market_sessions import is_us_regular_session


def test_rth_open_true():
    assert is_us_regular_session(datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)) is True


def test_premarket_false():
    assert is_us_regular_session(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)) is False


def test_afterhours_false():
    assert is_us_regular_session(datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc)) is False


def test_exactly_open_true():
    assert is_us_regular_session(datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)) is True


def test_exactly_close_false():
    assert is_us_regular_session(datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)) is False
