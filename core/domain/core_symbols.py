"""SSoT: 每市场"核心标的"——无条件常驻采集的 baseline。

采集路径(signal/fetch cron、sweep、US poller、启动 reconcile)的标的集 =
CORE_SYMBOLS[market] ∪ DB watchlist。前端默认展示列表(apps/web/app/page.tsx
DEFAULT_WATCHLIST)应为各市场 CORE 的子集(决策 a:纯后端对齐,约定同步)。
"""
from __future__ import annotations

CORE_SYMBOLS: dict[str, list[str]] = {
    "ashare": [
        "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
        "000905.SH", "000852.SH", "000688.SH", "000016.SH",
        "600519.SH", "300750.SZ", "002594.SZ", "603259.SH",
        "688981.SH", "002371.SZ", "300059.SZ",
    ],
    "us": [
        "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META", "AMD",
        "SPY", "QQQ", "DIA",
    ],
    "crypto": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT"],
    "hk": [],
}


def core_symbols(market: str) -> list[str]:
    """返回该市场核心标的列表;未知市场返回 []。"""
    return list(CORE_SYMBOLS.get(market, []))
