import pytest
import aiosqlite

from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo


@pytest.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "state.db")
    # 建表 (沿用 schema.sql 但只取 baseline 表的 DDL 简化测试)
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


async def test_upsert_and_query_prev_day(repo):
    # 写昨日 + 前日
    await repo.upsert_day("ashare", "2026-05-26", [(0, 100.0), (1, 250.0), (2, 380.0)])
    await repo.upsert_day("ashare", "2026-05-27", [(0, 120.0), (1, 290.0), (2, 410.0)])

    # 查上一交易日 = 2026-05-27 (相对 today 2026-05-28)
    v = await repo.query_prev_day_at_offset("ashare", "2026-05-28", 1)
    assert v == 290.0

    # offset 越界 → None
    v = await repo.query_prev_day_at_offset("ashare", "2026-05-28", 99)
    assert v is None

    # 不同市场不串
    v = await repo.query_prev_day_at_offset("hk", "2026-05-28", 1)
    assert v is None


async def test_upsert_overwrites_same_offset(repo):
    """同一 (market, date, offset) 二次写要 UPSERT 覆盖, 不抛 UNIQUE 冲突。"""
    await repo.upsert_day("ashare", "2026-05-27", [(0, 100.0)])
    await repo.upsert_day("ashare", "2026-05-27", [(0, 999.0)])
    v = await repo.query_prev_day_at_offset("ashare", "2026-05-28", 0)
    assert v == 999.0


async def test_query_prev_day_picks_most_recent(repo):
    """有多个历史日时取最近的 (DESC LIMIT 1)。"""
    await repo.upsert_day("ashare", "2026-05-20", [(0, 100.0)])
    await repo.upsert_day("ashare", "2026-05-26", [(0, 200.0)])
    await repo.upsert_day("ashare", "2026-05-27", [(0, 300.0)])

    v = await repo.query_prev_day_at_offset("ashare", "2026-05-28", 0)
    assert v == 300.0


async def test_query_avg_n_days(repo):
    """美股 Relative Volume 10D 路径: 平均最近 N 个交易日同 offset。"""
    for d, val in [
        ("2026-05-20", 100.0), ("2026-05-21", 110.0), ("2026-05-22", 120.0),
        ("2026-05-26", 130.0), ("2026-05-27", 140.0),
    ]:
        await repo.upsert_day("us", d, [(5, val)])

    # 取最近 3 天均: (140 + 130 + 120) / 3 = 130
    v = await repo.query_avg_n_days_at_offset("us", "2026-05-28", 5, n_days=3)
    assert v == pytest.approx(130.0)

    # 取最近 100 天 (实际只有 5 天) 均: 5 天的均
    v = await repo.query_avg_n_days_at_offset("us", "2026-05-28", 5, n_days=100)
    assert v == pytest.approx(120.0)

    # 无数据返 None
    v = await repo.query_avg_n_days_at_offset("us", "2026-05-28", 99)
    assert v is None


async def test_upsert_empty_returns_zero(repo):
    n = await repo.upsert_day("ashare", "2026-05-27", [])
    assert n == 0


async def test_cleanup(repo):
    """删除指定天数前的数据。"""
    from datetime import date, timedelta
    # 21 天前的日期 (应被删)
    old_date = (date.today() - timedelta(days=21)).isoformat()
    # 5 天前 (应保留)
    new_date = (date.today() - timedelta(days=5)).isoformat()

    await repo.upsert_day("ashare", old_date, [(0, 100.0)])
    await repo.upsert_day("ashare", new_date, [(0, 200.0)])

    deleted = await repo.cleanup_older_than(days=20)
    assert deleted == 1

    # 老数据不可查; 新数据仍在
    today = (date.today() + timedelta(days=1)).isoformat()
    v_old = await repo.query_prev_day_at_offset("ashare", today, 0)
    # 只剩 new_date, 应返 200 (已删 100)
    assert v_old == 200.0
