"""SSoT: 根据 symbol 字符串推断市场。

收口前散在:
- apps/api/routes/symbols.py::_infer_market
- apps/api/routes/cd_signals.py::_is_crypto
- core/scheduler/jobs.py::_market_of
"""
from __future__ import annotations

from typing import Literal

Market = Literal["ashare", "hk", "us", "crypto"]


def infer_market(symbol: str) -> Market:
    """根据 symbol 字符串推断市场。

    - 600519.SH / 510300.SH / 000001.SZ / 920001.BJ → ashare
    - 9988.HK / HSI.HK                              → hk
    - 含 '/' (如 BTC/USDT)                           → crypto
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
    return "us"


def is_crypto(symbol: str) -> bool:
    """symbol 是否属于 crypto 市场。"""
    return infer_market(symbol) == "crypto"
