from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.adapters.ashare import _is_in_ashare_intraday_session

_BJT = ZoneInfo("Asia/Shanghai")


def _bjt(h: int, m: int) -> datetime:
    """BJT wall-clock → UTC datetime (用 2026-05-28 周四作样本日期)。"""
    return datetime(2026, 5, 28, h, m, tzinfo=_BJT).astimezone(timezone.utc)


def test_pre_market_before_call_auction_is_off_session():
    # 00:00 ~ 09:14 全部 off-session
    assert _is_in_ashare_intraday_session(_bjt(0, 0)) is False
    assert _is_in_ashare_intraday_session(_bjt(8, 30)) is False
    assert _is_in_ashare_intraday_session(_bjt(9, 14)) is False


def test_call_auction_is_in_session():
    # 集合竞价 09:15 - 09:25 是真实数据, 保留
    assert _is_in_ashare_intraday_session(_bjt(9, 15)) is True
    assert _is_in_ashare_intraday_session(_bjt(9, 20)) is True
    assert _is_in_ashare_intraday_session(_bjt(9, 25)) is True


def test_morning_session_is_in_session():
    # 连续竞价上午 09:30 - 11:30
    assert _is_in_ashare_intraday_session(_bjt(9, 30)) is True
    assert _is_in_ashare_intraday_session(_bjt(10, 0)) is True
    assert _is_in_ashare_intraday_session(_bjt(11, 30)) is True


def test_lunch_break_is_off_session():
    # 午休 11:31 - 12:59
    assert _is_in_ashare_intraday_session(_bjt(11, 31)) is False
    assert _is_in_ashare_intraday_session(_bjt(12, 0)) is False
    assert _is_in_ashare_intraday_session(_bjt(12, 59)) is False


def test_afternoon_session_is_in_session():
    # 连续竞价下午 13:00 - 15:00 (含收盘集合竞价 14:57-15:00)
    assert _is_in_ashare_intraday_session(_bjt(13, 0)) is True
    assert _is_in_ashare_intraday_session(_bjt(14, 30)) is True
    assert _is_in_ashare_intraday_session(_bjt(14, 57)) is True
    assert _is_in_ashare_intraday_session(_bjt(15, 0)) is True


def test_post_close_is_off_session():
    # 15:01 之后全 off
    assert _is_in_ashare_intraday_session(_bjt(15, 1)) is False
    assert _is_in_ashare_intraday_session(_bjt(20, 0)) is False
    assert _is_in_ashare_intraday_session(_bjt(23, 59)) is False


def test_naive_utc_datetime_passes_through_tz_conversion():
    # 输入 UTC datetime, 函数内部转 BJT 判断
    # UTC 2026-05-28 01:30:00 = BJT 09:30 → in session
    utc = datetime(2026, 5, 28, 1, 30, tzinfo=timezone.utc)
    assert _is_in_ashare_intraday_session(utc) is True
    # UTC 2026-05-27 16:00:00 = BJT 2026-05-28 00:00 → off session
    utc = datetime(2026, 5, 27, 16, 0, tzinfo=timezone.utc)
    assert _is_in_ashare_intraday_session(utc) is False
