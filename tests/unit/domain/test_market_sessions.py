"""is_market_session_open 边界单测 — Plan: 防止 collector cron 在夜里跑。"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.domain.market_sessions import is_after_market_close, is_market_session_open


def _bjt(h: int, m: int = 0) -> datetime:
    return datetime(2026, 5, 28, h, m, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)


def _et(h: int, m: int = 0) -> datetime:
    return datetime(2026, 5, 28, h, m, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


@pytest.mark.parametrize("h,m,expected", [
    (8, 0, False),    # 盘前
    (9, 30, True),    # 开盘
    (11, 30, True),   # 上午收盘瞬间
    (12, 0, False),   # 午休
    (13, 0, True),    # 下午开盘
    (15, 0, True),    # 收盘瞬间
    (15, 1, False),   # 收盘后
    (18, 0, False),   # 晚上
    (3, 0, False),    # 凌晨
])
def test_ashare_session_boundaries(h, m, expected):
    assert is_market_session_open("ashare", _bjt(h, m)) is expected


@pytest.mark.parametrize("h,m,expected", [
    (3, 59, False),
    (4, 0, True),     # 盘前开始
    (9, 30, True),    # RTH 开始
    (16, 0, True),    # RTH 收盘
    (20, 0, True),    # 盘后收盘
    (20, 1, False),
    (0, 0, False),
])
def test_us_session_boundaries(h, m, expected):
    assert is_market_session_open("us", _et(h, m)) is expected


def test_crypto_always_open():
    # crypto 任意时刻都该 True (24/7)
    assert is_market_session_open("crypto", _bjt(3, 0)) is True
    assert is_market_session_open("crypto", _bjt(15, 0)) is True


@pytest.mark.parametrize("h,m,expected", [
    (10, 0, False),   # 盘中上午: 未过收盘
    (11, 30, False),  # 上午收盘瞬间: 还没到当日最后收盘(15:00)
    (12, 0, False),   # 午休: 关键! session_open=False 但未过当日收盘 → 不该触发结算
    (14, 0, False),   # 盘中下午
    (15, 0, False),   # 收盘瞬间(15:00): cur>last_close 才 True, 15:00 不算
    (15, 1, True),    # 收盘后
    (18, 0, True),    # 晚上
])
def test_ashare_after_market_close(h, m, expected):
    # 根治 daily_settlement 午休误触发: is_after_market_close 区分午休 vs 真收盘
    assert is_after_market_close("ashare", _bjt(h, m)) is expected


def test_crypto_never_after_close():
    assert is_after_market_close("crypto", _bjt(3, 0)) is False
