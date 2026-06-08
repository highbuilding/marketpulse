from core.domain.markets import infer_market, is_crypto, normalize_symbol


def test_infer_market_ashare_suffix():
    assert infer_market("600519.SH") == "ashare"
    assert infer_market("000001.SZ") == "ashare"
    assert infer_market("920001.BJ") == "ashare"
    assert infer_market("510300.SH") == "ashare"  # ETF


def test_infer_market_ashare_bare_code():
    # 裸 6 位数字码识别为 A 股(根因: 否则兜底成 us + profile 查不到名称)
    assert infer_market("002415") == "ashare"
    assert infer_market("600519") == "ashare"
    assert infer_market("000858") == "ashare"


def test_normalize_symbol_ashare_bare_code():
    # 裸码补后缀: 与 symbol_directory key 对齐
    assert normalize_symbol("600519") == "600519.SH"
    assert normalize_symbol("688981") == "688981.SH"  # 科创
    assert normalize_symbol("510300") == "510300.SH"  # 沪 ETF
    assert normalize_symbol("002415") == "002415.SZ"
    assert normalize_symbol("000858") == "000858.SZ"
    assert normalize_symbol("920001") == "920001.BJ"  # 北交所新段
    assert normalize_symbol("830799") == "830799.BJ"  # 北交所 8 开头


def test_normalize_symbol_passthrough():
    # 已带后缀 / 非 A 股裸码 / 其他市场: 原样返回
    assert normalize_symbol("600519.SH") == "600519.SH"
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("BRK.B") == "BRK.B"
    assert normalize_symbol("BTC-USDT") == "BTC-USDT"
    assert normalize_symbol("9988.HK") == "9988.HK"
    assert normalize_symbol("12345") == "12345"   # 非 6 位不动
    assert normalize_symbol("1234567") == "1234567"


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
