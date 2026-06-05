"""APP_ENV 标的集分层契约测试。

test/prod 两套字典在 import 时已定型, 这里直接验静态字典(不依赖 APP_ENV
运行时切换, 因 CORE_SYMBOLS 是 import 时定型的模块级常量)。
"""
from core.domain.core_symbols import CORE_TEST, CORE_PROD
from core.domain._hs300 import HS300_SYMBOLS
from core.domain._sp100 import SP100_SYMBOLS


def test_test_env_each_market_30():
    """test 精简集: A股/美股各 30, crypto 5。"""
    assert len(CORE_TEST["ashare"]) == 30
    assert len(CORE_TEST["us"]) == 30
    assert len(CORE_TEST["crypto"]) == 5


def test_prod_env_covers_hs300_and_sp100():
    """prod: A股 ⊇ 沪深300, 美股 ⊇ 标普100。"""
    assert set(HS300_SYMBOLS) <= set(CORE_PROD["ashare"])
    assert set(SP100_SYMBOLS) <= set(CORE_PROD["us"])
    # 8 大指数 + 3 ETF 也在 prod
    assert "000300.SH" in CORE_PROD["ashare"]
    assert "SPY" in CORE_PROD["us"]


def test_no_duplicates_in_any_env():
    for d in (CORE_TEST, CORE_PROD):
        for market, syms in d.items():
            assert len(syms) == len(set(syms)), f"{market} 有重复"


def test_crypto_unchanged_across_envs():
    """crypto 两环境一致(spec: crypto 不动)。"""
    assert CORE_TEST["crypto"] == CORE_PROD["crypto"]


def test_hs300_snapshot_integrity():
    """沪深300 静态清单: 300 只无重复。"""
    assert len(HS300_SYMBOLS) == 300
    assert len(set(HS300_SYMBOLS)) == 300


def test_sp100_snapshot_integrity():
    """标普100 静态清单: ~100 只无重复, 裸 ticker 口径。"""
    assert 95 <= len(SP100_SYMBOLS) <= 105
    assert len(set(SP100_SYMBOLS)) == len(SP100_SYMBOLS)
