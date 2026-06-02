import pytest

from core.cache import keys


def test_cache_quote_key():
    assert keys.cache_quote("ashare", "600519.SH") == "cache:quote:ashare:600519.SH"


def test_cache_index_minute_key():
    assert keys.cache_index_minute("000001.SH", days=1) == "cache:index:000001.SH:minute:1"
    assert keys.cache_index_minute("000001.SH", days=5) == "cache:index:000001.SH:minute:5"


def test_cache_market_dashboard_key():
    assert keys.cache_market_dashboard("ashare") == "cache:market:ashare:dashboard"


def test_cache_market_top_key():
    assert keys.cache_market_top("ashare") == "cache:market:ashare:top"
    assert keys.cache_market_top("hk") == "cache:market:hk:top"


def test_cache_market_ai_packet_key():
    assert keys.cache_market_ai_packet("ashare") == "cache:market:ashare:ai_packet"


def test_cache_chip_summary_key():
    assert keys.cache_chip_summary("600519.SH", days=90) == "cache:chip:600519.SH:90d"


def test_cache_bars_tail_key():
    assert (
        keys.cache_bars_tail("ashare", "600519.SH", "1d")
        == "cache:bars:ashare:600519.SH:1d:tail"
    )


def test_cache_bars_full_key():
    assert (
        keys.cache_bars_full("ashare", "600519.SH", "1d", "abc123")
        == "cache:bars:ashare:600519.SH:1d:full:abc123"
    )


def test_cache_fundflow_key():
    assert keys.cache_fundflow("600519.SH", days=30) == "cache:fundflow:600519.SH:30d"


def test_state_leader_collector_key():
    assert keys.state_leader_collector() == "state:leader:collector"


def test_state_source_key():
    assert keys.state_source("sina") == "state:source:sina"


def test_state_outlet_key():
    assert keys.state_outlet("local") == "state:outlet:local"


def test_state_inflight_key():
    assert keys.state_inflight("bars:600519.SH:1d") == "state:inflight:bars:600519.SH:1d"


def test_ratelimit_source_key():
    assert keys.ratelimit_source("sina") == "ratelimit:source:sina"


def test_ratelimit_outlet_key():
    assert keys.ratelimit_outlet("local") == "ratelimit:outlet:local"


def test_bus_topic_constants():
    assert keys.BUS_QUOTE_TICK == "bus:quote.tick"
    assert keys.BUS_BARS_UPDATED == "bus:bars.updated"
    assert keys.BUS_SIGNAL_NEW == "bus:signal.new"
    assert keys.BUS_SOURCE_STATUS == "bus:source.status"
    assert keys.BUS_BARS_REFILL_REQUEST == "bus:bars.refill_request"


def test_validate_key_rejects_single_segment():
    with pytest.raises(ValueError, match="must be at least 2 segments"):
        keys.validate("foo")


def test_validate_key_rejects_unknown_namespace():
    with pytest.raises(ValueError, match="unknown namespace"):
        keys.validate("foobar:xxx")


def test_validate_key_accepts_well_formed():
    keys.validate("cache:quote:ashare:600519.SH")
    keys.validate("state:source:sina")
    keys.validate("bus:quote.tick")
    keys.validate("ratelimit:source:sina")


def test_bus_intraday_updated_constant():
    assert keys.BUS_INTRADAY_UPDATED == "bus:intraday.updated"


def test_cache_intraday_current_key():
    k = keys.cache_intraday_current("ashare", "600519.SH")
    assert k == "cache:intraday:ashare:600519.SH:current"
    keys.validate(k)  # 不抛


def test_cache_barspage_key():
    from core.cache import keys
    k = keys.cache_barspage("us", "AAPL", "5m", "2026-06-01T00:00:00", 500)
    assert k == "cache:barspage:us:AAPL:5m:2026-06-01T00:00:00:500"
    keys.validate(k)


def test_cache_barspage_latest_uses_marker():
    from core.cache import keys
    k = keys.cache_barspage("us", "AAPL", "5m", None, 500)
    assert k.endswith(":latest:500")
    keys.validate(k)
