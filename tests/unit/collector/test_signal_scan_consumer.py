from apps.collector.jobs.signal_scan_consumer import should_scan


def test_should_scan_final_signal_interval():
    assert should_scan({"final": True, "interval": "4h"}) is True
    assert should_scan({"final": True, "interval": "15m"}) is True
    assert should_scan({"final": True, "interval": "1d"}) is True


def test_should_scan_skips_in_progress():
    assert should_scan({"final": False, "interval": "4h"}) is False


def test_should_scan_skips_non_signal_interval():
    assert should_scan({"final": True, "interval": "1m"}) is False
    assert should_scan({"final": True, "interval": "1wk"}) is False
    assert should_scan({"final": True, "interval": "1mo"}) is False


def test_should_scan_missing_fields():
    assert should_scan({}) is False
    assert should_scan({"interval": "4h"}) is False
    assert should_scan({"final": True}) is False
