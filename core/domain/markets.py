"""SSoT: 根据 symbol 字符串推断市场。

收口前散在:
- apps/api/routes/symbols.py::_infer_market
- apps/api/routes/cd_signals.py::_is_crypto
- core/scheduler/jobs.py::_market_of
"""
from __future__ import annotations

from typing import Literal

Market = Literal["ashare", "hk", "us", "crypto"]

# crypto 计价稳定币后缀 (与 core/adapters/binance.py::_from_binance 同步)
_CRYPTO_QUOTE_SUFFIXES = ("-USDT", "-USDC", "-BUSD", "-FDUSD")


def normalize_symbol(symbol: str) -> str:
    """裸 A 股 6 位数字码补后缀, 其余原样返回。

    600519 → 600519.SH, 000858 → 000858.SZ, 920001 → 920001.BJ。
    根因: 用户/前端常输入不带后缀的裸码 ("002415"), 而 symbol_directory 的
    key 带后缀 ("002415.SZ") → get_name 查不到 + infer_market 兜底成 us。
    在系统入口 (profile/quote 路由) 归一化, 让下游 name 查询 + 市场推断都正确。

    仅处理"纯 6 位数字"(A 股唯一裸码形态; US ticker 含字母, HK/crypto 已带标识),
    歧义最小。前缀规则与 symbol_directory_service._normalize_ashare 同源。
    """
    if len(symbol) == 6 and symbol.isdigit():
        if symbol.startswith(("60", "68", "51", "50", "11", "13")):
            return f"{symbol}.SH"
        if symbol.startswith(("4", "8", "920")):
            return f"{symbol}.BJ"
        return f"{symbol}.SZ"
    return symbol


def infer_market(symbol: str) -> Market:
    """根据 symbol 字符串推断市场。

    - 600519.SH / 510300.SH / 000001.SZ / 920001.BJ → ashare
    - 600519 / 002415 等裸 6 位数字码                → ashare(经 normalize_symbol 补后缀)
    - 9988.HK / HSI.HK                              → hk
    - 含 '/' (如 BTC/USDT) 或以 -USDT/-USDC 结尾    → crypto
      (Binance adapter 项目内 symbol 用 BTC-USDT 形式)
    - 其他 (AAPL / BRK.B / SPY / ^GSPC)             → us(兜底)

    注意: 白名单优先(.SH/.SZ/.BJ/.HK), 不要写成"含点号即非 us"的黑名单,
    否则 BRK.B 会被误判。
    """
    if symbol.endswith((".SH", ".SZ", ".BJ")):
        return "ashare"
    if symbol.endswith(".HK"):
        return "hk"
    if "/" in symbol:
        return "crypto"
    if symbol.endswith(_CRYPTO_QUOTE_SUFFIXES):
        return "crypto"
    if len(symbol) == 6 and symbol.isdigit():
        return "ashare"
    return "us"


def is_crypto(symbol: str) -> bool:
    """symbol 是否属于 crypto 市场。"""
    return infer_market(symbol) == "crypto"
