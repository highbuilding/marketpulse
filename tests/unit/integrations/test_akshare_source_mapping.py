from core.integrations.akshare import _infer_source


def test_em_daily_fallback_functions_are_mapped_to_em_source():
    assert _infer_source("stock_zh_a_hist") == "em"
    assert _infer_source("stock_zh_index_daily_em") == "em"


def test_unmapped_akshare_functions_default_to_sina():
    assert _infer_source("stock_zh_a_daily") == "sina"
