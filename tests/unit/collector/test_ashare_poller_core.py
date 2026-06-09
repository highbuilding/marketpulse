def test_build_core_active_is_core_5m_only():
    from apps.collector.ashare import bar_poller as bp
    from core.domain.core_symbols import CORE_SYMBOLS
    active = bp._build_core_active()
    expected = {f"{s}:5m" for s in CORE_SYMBOLS["ashare"]}
    assert active == expected
