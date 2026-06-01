from apps.collector.ashare.bar_poller import INTERVAL_TO_PERIOD


def test_1m_removed_from_poller():
    assert "1m" not in INTERVAL_TO_PERIOD


def test_poller_periods_are_5_15_30():
    assert set(INTERVAL_TO_PERIOD) == {"5m", "15m", "30m"}
