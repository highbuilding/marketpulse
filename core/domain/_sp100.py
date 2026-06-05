"""标普100 (S&P 100 / OEX) 成分股静态清单 (prod CORE 美股标的)。

akshare 无可靠的标普100 成分接口, 故手写常量。标普100 是标普500 中
最大、最稳定的 ~101 只蓝筹(部分公司双类别股, 如 GOOG/GOOGL)。
成分定期调整, 需手动维护 —— 见 spec 风险表。
快照基准: 2025 年末公开成分。symbol 用 Alpaca/项目口径 (裸 ticker)。
"""
from __future__ import annotations

# 标普100 成分 (~101 只)
SP100_SYMBOLS: list[str] = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT", "AMZN",
    "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK.B", "C",
    "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS",
    "CVX", "DE", "DHR", "DIS", "DOW", "DUK", "EMR", "FDX", "GD", "GE",
    "GILD", "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU",
    "ISRG", "JNJ", "JPM", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MCD",
    "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE",
    "NFLX", "NKE", "NVDA", "ORCL", "PEP", "PFE", "PG", "PM", "PYPL", "QCOM",
    "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA",
    "TXN", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC", "WMT", "XOM",
    "LRCX",
]
