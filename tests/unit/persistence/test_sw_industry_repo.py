from __future__ import annotations

import pytest

from core.domain.models import SwIndustryBar, SwIndustryInfo
from core.persistence.sqlite_repo import StateRepo  # noqa: F401 (ensure path)
from core.persistence.sw_industry_repo import SwIndustryRepo


@pytest.fixture
async def repo(tmp_path):
    db = tmp_path / "state.db"
    # 建表 (复用全 schema)
    from core.persistence.sqlite_repo import StateRepo as _SR
    await _SR(str(db)).init()
    return SwIndustryRepo(str(db))


@pytest.mark.asyncio
async def test_save_and_list_history(repo):
    bars = [
        SwIndustryBar(industry_code="801010", industry_name="农林牧渔",
                      trade_date="2026-01-05", open=100, high=105, low=99, close=104,
                      volume=1.0, amount=2.0),
        SwIndustryBar(industry_code="801010", industry_name="农林牧渔",
                      trade_date="2026-01-06", open=104, high=110, low=103, close=108,
                      volume=1.5, amount=3.0),
    ]
    n = await repo.save_bars(bars)
    assert n == 2
    rows = await repo.list_history("801010", start="2026-01-01", end="2026-01-31")
    assert [r.trade_date for r in rows] == ["2026-01-05", "2026-01-06"]
    assert rows[-1].close == 108

    # 区间过滤
    rows2 = await repo.list_history("801010", start="2026-01-06", end="2026-01-31")
    assert len(rows2) == 1


@pytest.mark.asyncio
async def test_save_bars_idempotent_upsert(repo):
    bar = SwIndustryBar(industry_code="801030", trade_date="2026-02-01", close=200)
    await repo.save_bars([bar])
    # 重复写同 (code, date) → 更新不重复
    await repo.save_bars([SwIndustryBar(industry_code="801030", trade_date="2026-02-01", close=210)])
    rows = await repo.list_history("801030")
    assert len(rows) == 1
    assert rows[0].close == 210


@pytest.mark.asyncio
async def test_last_dates(repo):
    await repo.save_bars([
        SwIndustryBar(industry_code="801010", trade_date="2026-01-05", close=1),
        SwIndustryBar(industry_code="801010", trade_date="2026-01-08", close=2),
        SwIndustryBar(industry_code="801030", trade_date="2026-01-07", close=3),
    ])
    last = await repo.last_dates()
    assert last["801010"] == "2026-01-08"
    assert last["801030"] == "2026-01-07"


@pytest.mark.asyncio
async def test_save_and_list_info(repo):
    infos = [
        SwIndustryInfo(industry_code="801010", industry_name="农林牧渔",
                       member_count=104, pe_static=21.6, pb=2.0),
    ]
    assert await repo.save_info(infos) == 1
    rows = await repo.list_info()
    assert rows[0].industry_name == "农林牧渔"
    assert rows[0].member_count == 104
