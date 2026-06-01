from core.domain.intervals import INTERVAL_CONFIG, KLINE_INTERVALS


def test_1m_not_kline():
    assert "1m" not in KLINE_INTERVALS


def test_1m_spec_still_exists_but_hidden():
    # 1m spec 保留(历史数据兼容)但不暴露给 K 线
    assert INTERVAL_CONFIG["1m"].is_kline is False
