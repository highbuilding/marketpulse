"""is_market_session_open 边界单测 — Plan: 防止 collector cron 在夜里跑。"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.domain.market_sessions import is_market_session_open


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
