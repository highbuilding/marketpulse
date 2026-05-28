from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.domain.market_calendar import (
    is_trading_day,
    is_trading_now,
    _get_calendar,
)


def test_crypto_always_trading():
    assert is_trading_day("crypto") is True
    assert is_trading_day("crypto", date(2026, 1, 1)) is True
    assert is_trading_now("crypto") is True


def test_unknown_market_returns_false():
    assert is_trading_day("xxx") is False


def test_ashare_lunar_new_year_2026_is_holiday():
    # 春节 2026-02-17 周二 — 上交所休市
    assert is_trading_day("ashare", date(2026, 2, 17)) is False


def test_ashare_normal_weekday_is_trading():
    # 2026-05-28 周四 普通交易日
    assert is_trading_day("ashare", date(2026, 5, 28)) is True


def test_ashare_weekend_is_not_trading():
    # 2026-05-31 周日
    assert is_trading_day("ashare", date(2026, 5, 31)) is False


def test_us_independence_day_observed_2026_holiday():
    # 2026-07-04 周六 → 7/3 周五 observed
    assert is_trading_day("us", date(2026, 7, 3)) is False


def test_us_normal_weekday_is_trading():
    # 2026-05-28 周四 NYSE 正常交易
    assert is_trading_day("us", date(2026, 5, 28)) is True


def test_hk_qingming_2026_holiday():
    # 2026-04-06 周一 (清明假期顺延) — HKEX 休市
    assert is_trading_day("hk", date(2026, 4, 6)) is False


def test_hk_normal_weekday_is_trading():
    assert is_trading_day("hk", date(2026, 5, 28)) is True


def test_calendar_lru_cache_singleton():
    # 同一市场重复获取应返回同一实例
    cal1 = _get_calendar("ashare")
    cal2 = _get_calendar("ashare")
    assert cal1 is cal2


def test_is_trading_day_naive_datetime_treated_as_market_local():
    # 给一个 naive 时刻(没 tzinfo),应按市场本地时区解析
    # 2026-05-28 任何时刻都是工作日
    naive = datetime(2026, 5, 28, 12, 0)
    assert is_trading_day("ashare", naive) is True


def test_is_trading_day_aware_datetime_converted_to_local():
    # UTC 2026-05-31 16:00 = BJT 2026-06-01 00:00 (周一,交易日)
    utc_dt = datetime(2026, 5, 31, 16, 0, tzinfo=ZoneInfo("UTC"))
    assert is_trading_day("ashare", utc_dt) is True
