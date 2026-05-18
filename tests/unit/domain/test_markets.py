from core.domain.markets import infer_market, is_crypto


def test_infer_market_ashare_suffix():
    assert infer_market("600519.SH") == "ashare"
    assert infer_market("000001.SZ") == "ashare"
    assert infer_market("920001.BJ") == "ashare"
    assert infer_market("510300.SH") == "ashare"  # ETF


def test_infer_market_hk_suffix():
    assert infer_market("9988.HK") == "hk"
    assert infer_market("HSI.HK") == "hk"


def test_infer_market_crypto():
    assert infer_market("BTC/USDT") == "crypto"
    assert infer_market("ETH/USDT") == "crypto"


def test_infer_market_us_default():
    assert infer_market("AAPL") == "us"
    assert infer_market("BRK.B") == "us"  # 点号但不在白名单 → us
    assert infer_market("^GSPC") == "us"  # 美股指数
    assert infer_market("SPY") == "us"


def test_is_crypto():
    assert is_crypto("BTC/USDT")
    assert not is_crypto("AAPL")
    assert not is_crypto("600519.SH")
