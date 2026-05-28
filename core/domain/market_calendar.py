"""市场交易日历 — 判断指定市场某天是否为交易日 / 当下是否在交易时段。

用 exchange_calendars 包覆盖 A 股 / 港股 / 美股 / Crypto 4 市场:
- A 股 (XSHG): 上交所历年节假日 + 调休
- 港股 (XHKG): HKEX
- 美股 (XNYS): NYSE/NASDAQ
- Crypto: 7×24 永远开盘

参考: exchange_calendars 文档 https://github.com/gerrymanoim/exchange_calendars
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import structlog

log = structlog.get_logger(__name__)

_MARKET_TO_CAL = {
    "ashare": "XSHG",
    "hk": "XHKG",
    "us": "XNYS",
}

_MARKET_TO_TZ = {
    "ashare": ZoneInfo("Asia/Shanghai"),
    "hk": ZoneInfo("Asia/Hong_Kong"),
    "us": ZoneInfo("America/New_York"),
}


@lru_cache(maxsize=4)
def _get_calendar(market: str):
    """exchange_calendars 单例(进程内 lru_cache)。

    init 一次需要 ~50-200 ms 加载历年假期表, 所以 cache 起来。
    """
    cal_name = _MARKET_TO_CAL.get(market)
    if cal_name is None:
        return None
    import exchange_calendars  # noqa: PLC0415 — 延迟导入,加载快
    return exchange_calendars.get_calendar(cal_name)


def is_trading_day(market: str, when: datetime | date | None = None) -> bool:
    """指定市场指定日期是否为交易日。

    crypto 永远 True。未知市场默认 False(保守, 不浪费 ak 调用)。
    when=None 取当前时刻在该市场本地时区的日期。
    """
    if market == "crypto":
        return True
    cal = _get_calendar(market)
    if cal is None:
        log.warning("market_calendar.unknown_market", market=market)
        return False
    tz = _MARKET_TO_TZ.get(market)
    if when is None:
        when = datetime.now(tz)
    if isinstance(when, datetime):
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
        local_date = when.astimezone(tz).date()
    else:
        local_date = when
    try:
        return bool(cal.is_session(local_date.strftime("%Y-%m-%d")))
    except Exception as e:  # noqa: BLE001
        # 日期超出 calendar 数据范围(如 2050 年)→ 保守按非交易日处理
        log.warning("market_calendar.is_session_failed",
                    market=market, date=str(local_date), error=str(e))
        return False


def is_trading_now(market: str) -> bool:
    """当下时刻是否在该市场的交易时段(含开盘 + 含收盘)。

    crypto 永远 True; 节假日永远 False; 工作日中盘前/盘后/午休都返回 False。
    用于"是否要打 ak 拿实时 quote"这类决策(节假日早返跳过)。
    """
    if market == "crypto":
        return True
    cal = _get_calendar(market)
    if cal is None:
        return False
    tz = _MARKET_TO_TZ.get(market)
    now = datetime.now(tz)
    if not is_trading_day(market, now):
        return False
    try:
        # exchange_calendars open/close in UTC tz-aware timestamps
        ts = now.astimezone(ZoneInfo("UTC"))
        # is_open_at_time 接受 pd.Timestamp / datetime
        return bool(cal.is_open_at_time(ts))
    except Exception as e:  # noqa: BLE001
        log.warning("market_calendar.is_open_failed", market=market, error=str(e))
        # 降级: 判定为交易日就算 trading_now,不阻塞 job
        return True
