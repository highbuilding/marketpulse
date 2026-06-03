"""Crypto directory 种子: 主流币种(USDT 计价)。
不调外部 API, 启动期纯本地写库。symbol 用 BASE-USDT 口径(与 collector core_symbols 一致)。
搜索匹配 symbol LIKE 'BTC%' → 用户搜 BTC 命中 BTC-USDT。
"""
from __future__ import annotations

CRYPTO_SEEDS: list[tuple[str, str, str]] = [
    ("BTC-USDT", "Bitcoin", "crypto"),
    ("ETH-USDT", "Ethereum", "crypto"),
    ("SOL-USDT", "Solana", "crypto"),
    ("XRP-USDT", "XRP", "crypto"),
    ("TRX-USDT", "TRON", "crypto"),
    ("BNB-USDT", "BNB", "crypto"),
    ("DOGE-USDT", "Dogecoin", "crypto"),
    ("ADA-USDT", "Cardano", "crypto"),
    ("AVAX-USDT", "Avalanche", "crypto"),
    ("LINK-USDT", "Chainlink", "crypto"),
    ("DOT-USDT", "Polkadot", "crypto"),
    ("MATIC-USDT", "Polygon", "crypto"),
    ("LTC-USDT", "Litecoin", "crypto"),
    ("BCH-USDT", "Bitcoin Cash", "crypto"),
    ("UNI-USDT", "Uniswap", "crypto"),
    ("ATOM-USDT", "Cosmos", "crypto"),
    ("ETC-USDT", "Ethereum Classic", "crypto"),
    ("FIL-USDT", "Filecoin", "crypto"),
    ("APT-USDT", "Aptos", "crypto"),
    ("ARB-USDT", "Arbitrum", "crypto"),
    ("OP-USDT", "Optimism", "crypto"),
    ("NEAR-USDT", "NEAR Protocol", "crypto"),
    ("INJ-USDT", "Injective", "crypto"),
    ("SUI-USDT", "Sui", "crypto"),
    ("TON-USDT", "Toncoin", "crypto"),
]
