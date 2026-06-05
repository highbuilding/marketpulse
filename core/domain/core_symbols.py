"""SSoT: 每市场"核心标的"——无条件常驻采集的 baseline。

采集路径(signal/fetch cron、sweep、US poller、启动 reconcile)的标的集 =
CORE_SYMBOLS[market] ∪ DB watchlist。前端默认展示列表(apps/web/app/page.tsx
DEFAULT_WATCHLIST)应为各市场 CORE 的子集(决策 a:纯后端对齐,约定同步)。

环境分层(2026-06-05, spec env-tiering):APP_ENV 选 test/prod 两套字典。
- test(本地默认):每市场 ~30 精简集(大盘指数 + 头部个股), crypto 5 币。
- prod(线上 export APP_ENV=prod):A股沪深300 + 美股标普100 + 大盘指数, ~400。
CORE_SYMBOLS 在 import 时按 APP_ENV 定型, 下游(core_symbols / CORE_SYMBOLS)无感。
"""
from __future__ import annotations

from core.domain._hs300 import HS300_SYMBOLS
from core.domain._sp100 import SP100_SYMBOLS
from core.domain.runtime_env import is_prod

# A股 8 大指数(test/prod 都常驻)
_ASHARE_INDICES = [
    "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
    "000905.SH", "000852.SH", "000688.SH", "000016.SH",
]
# 美股 3 大盘 ETF(test/prod 都常驻)
_US_ETFS = ["SPY", "QQQ", "DIA"]

# ── test 精简集(本地, 标的少高频轮询无压力)──
# A股: 8 指数 + 22 头部个股 = 30
CORE_TEST: dict[str, list[str]] = {
    "ashare": _ASHARE_INDICES + [
        "600519.SH", "300750.SZ", "002594.SZ", "603259.SH", "688981.SH",
        "002371.SZ", "300059.SZ", "600036.SH", "601318.SH", "600900.SH",
        "000858.SZ", "002415.SZ", "600276.SH", "601899.SH", "000333.SZ",
        "600030.SH", "601012.SH", "300760.SZ", "002475.SZ", "600887.SH",
        "601166.SH", "000001.SZ",
    ],
    "us": _US_ETFS + [
        "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META", "AMD",
        "GOOGL", "AVGO", "NFLX", "JPM", "V", "UNH", "XOM", "WMT",
        "COST", "ORCL", "CRM", "ADBE", "INTC", "QCOM", "CSCO",
        "PEP", "KO", "DIS", "BA", "GE",
    ],
    "crypto": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT"],
    "hk": [],
}

# ── prod 全量集(线上, 受 sina 限频约束, 轮询间隔拉长见 runtime_env)──
# A股: 8 指数 + 沪深300(去重并集, ~308); 美股: 3 ETF + 标普100(~104)
CORE_PROD: dict[str, list[str]] = {
    "ashare": sorted(set(_ASHARE_INDICES) | set(HS300_SYMBOLS)),
    "us": sorted(set(_US_ETFS) | set(SP100_SYMBOLS)),
    "crypto": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT"],
    "hk": [],
}

# APP_ENV 定型(import 时一次性选择)
CORE_SYMBOLS: dict[str, list[str]] = CORE_PROD if is_prod() else CORE_TEST


def core_symbols(market: str) -> list[str]:
    """返回该市场核心标的列表;未知市场返回 []。"""
    return list(CORE_SYMBOLS.get(market, []))
