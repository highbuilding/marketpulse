from core.domain.core_symbols import CORE_SYMBOLS, core_symbols


def test_us_core_contains_defaults_and_etfs():
    us = set(core_symbols("us"))
    assert {"AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META", "AMD"} <= us
    assert {"SPY", "QQQ", "DIA"} <= us


def test_ashare_core_contains_indices_and_default_stocks():
    a = set(core_symbols("ashare"))
    assert "000001.SH" in a and "600519.SH" in a and "300750.SZ" in a


def test_unknown_market_returns_empty():
    assert core_symbols("hk") == []
    assert core_symbols("nonexistent") == []
