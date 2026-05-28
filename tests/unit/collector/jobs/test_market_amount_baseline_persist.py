import pytest
import pandas as pd
import aiosqlite
from datetime import datetime
from zoneinfo import ZoneInfo

from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo
from apps.collector.jobs.market_amount_baseline_persist import (
    persist_ashare_baseline, _ashare_offset_from_dt, cleanup_old_baselines,
)


@pytest.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "state.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE market_amount_baseline (
              market TEXT NOT NULL,
              trading_date TEXT NOT NULL,
              ts_5m_offset INTEGER NOT NULL,
              cum_amount REAL NOT NULL,
              PRIMARY KEY (market, trading_date, ts_5m_offset)
            )
        """)
        await db.commit()
    yield MarketAmountBaselineRepo(db_path)


def test_offset_from_dt_morning_session():
    cn = ZoneInfo("Asia/Shanghai")
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 9, 30, tzinfo=cn)) == 0
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 9, 35, tzinfo=cn)) == 1
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 11, 25, tzinfo=cn)) == 23


def test_offset_from_dt_afternoon_session():
    cn = ZoneInfo("Asia/Shanghai")
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 13, 0, tzinfo=cn)) == 24
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 14, 55, tzinfo=cn)) == 47
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 11, 30, tzinfo=cn)) == 23
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 15, 0, tzinfo=cn)) == 47


def test_offset_from_dt_off_hours_returns_none():
    cn = ZoneInfo("Asia/Shanghai")
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 9, 0, tzinfo=cn)) is None
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 12, 0, tzinfo=cn)) is None
    assert _ashare_offset_from_dt(datetime(2026, 5, 28, 16, 0, tzinfo=cn)) is None


async def test_persist_ashare_writes_cumulative_curve(repo, monkeypatch):
    """模拟 5m 序列, 验证累加正确, 非今日数据被过滤。"""
    cn = ZoneInfo("Asia/Shanghai")
    # 模拟今日交易日
    today = datetime.now(cn).date().isoformat()

    rows = []
    for i, hour_minute in enumerate([(9, 30), (9, 35), (9, 40)]):
        rows.append({
            "day": f"{today} {hour_minute[0]:02d}:{hour_minute[1]:02d}:00",
            "open": 4090.0, "high": 4100.0, "low": 4085.0, "close": 4095.0,
            "volume": 1000000, "amount": 1.0e9 * (i + 1),  # 10亿, 20亿, 30亿
        })
    fake_df = pd.DataFrame(rows)

    async def fake_ak_call(*args, **kwargs):
        return fake_df

    monkeypatch.setattr(
        "apps.collector.jobs.market_amount_baseline_persist.ak_call", fake_ak_call,
    )

    # is_trading_day mock 为 True (无视周末)
    monkeypatch.setattr(
        "core.domain.market_calendar.is_trading_day", lambda *a, **k: True,
    )

    n = await persist_ashare_baseline(repo)
    assert n == 3

    # 累加验证: offset 0 = 10亿, offset 1 = 30亿, offset 2 = 60亿
    v0 = await repo.query_prev_day_at_offset(
        "ashare", (datetime.now(cn).date()).isoformat() + "_dummy_future", 0,
    )
    # 用未来日期触发"严格小于" 不行因为格式化不对, 用真未来日
    from datetime import timedelta as _td
    future = (datetime.now(cn).date() + _td(days=1)).isoformat()
    v0 = await repo.query_prev_day_at_offset("ashare", future, 0)
    v1 = await repo.query_prev_day_at_offset("ashare", future, 1)
    v2 = await repo.query_prev_day_at_offset("ashare", future, 2)
    assert v0 == pytest.approx(1.0e9)
    assert v1 == pytest.approx(3.0e9)
    assert v2 == pytest.approx(6.0e9)


async def test_persist_ashare_skips_non_trading_day(repo, monkeypatch):
    monkeypatch.setattr(
        "core.domain.market_calendar.is_trading_day", lambda *a, **k: False,
    )
    n = await persist_ashare_baseline(repo)
    assert n == 0


async def test_persist_ashare_handles_ak_failure(repo, monkeypatch):
    monkeypatch.setattr(
        "core.domain.market_calendar.is_trading_day", lambda *a, **k: True,
    )

    async def fake_ak_call(*args, **kwargs):
        raise RuntimeError("akshare timeout")

    monkeypatch.setattr(
        "apps.collector.jobs.market_amount_baseline_persist.ak_call", fake_ak_call,
    )
    n = await persist_ashare_baseline(repo)
    assert n == 0


async def test_cleanup_calls_repo(repo):
    """cleanup 委托给 repo, 简单 smoke。"""
    deleted = await cleanup_old_baselines(repo, days=20)
    assert deleted == 0  # 空表
