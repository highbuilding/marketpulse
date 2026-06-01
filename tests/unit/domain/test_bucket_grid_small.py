from datetime import date
from core.domain.market_sessions import bucket_grid


def test_ashare_5m_buckets_count():
    # A 股 240 分钟 / 5 = 48 根
    grid = bucket_grid("ashare", date(2026, 6, 1), 5)
    assert len(grid) == 48


def test_ashare_5m_first_bucket_close_0935():
    grid = bucket_grid("ashare", date(2026, 6, 1), 5)
    open_utc, close_utc = grid[0]
    # 首根 09:30-09:35 BJT, close = 09:35 BJT = 01:35 UTC
    assert close_utc.hour == 1 and close_utc.minute == 35


def test_ashare_15m_buckets_count():
    assert len(bucket_grid("ashare", date(2026, 6, 1), 15)) == 16
