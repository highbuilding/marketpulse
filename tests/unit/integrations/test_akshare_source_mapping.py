from core.integrations.akshare import _infer_source


def test_em_daily_fallback_functions_are_mapped_to_em_source():
    assert _infer_source("stock_zh_a_hist") == "em"
    assert _infer_source("stock_zh_index_daily_em") == "em"


def test_conclusion_layer_em_functions_are_mapped_to_em_source():
    for name in [
        "stock_zt_pool_em",
        "stock_zt_pool_previous_em",
        "stock_zt_pool_zbgc_em",
        "stock_zt_pool_dtgc_em",
        "stock_changes_em",
        "stock_board_change_em",
        "stock_lhb_detail_em",
        "stock_lhb_stock_statistic_em",
        "stock_notice_report",
    ]:
        assert _infer_source(name) == "em"


def test_ths_fund_flow_functions_are_mapped_to_ths_source():
    assert _infer_source("stock_fund_flow_individual") == "ths"
    assert _infer_source("stock_fund_flow_concept") == "ths"
    assert _infer_source("stock_fund_flow_industry") == "ths"


def test_unmapped_akshare_functions_default_to_sina():
    assert _infer_source("stock_zh_a_daily") == "sina"
