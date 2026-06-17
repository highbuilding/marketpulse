from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.domain.models import LimitPoolItem
from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return LimitPoolRepo(str(tmp_path / "state.db"))


def _item(symbol: str, pool_type: str, *, ladder: int | None = None) -> LimitPoolItem:
    return LimitPoolItem(
        market="ashare",
        trade_date="2026-06-17",
        pool_type=pool_type,
        symbol=symbol,
        name=symbol,
        amount=100_000_000,
        break_count=1 if pool_type == "broken_limit" else 0,
        ladder_count=ladder,
        pulled_at=datetime(2026, 6, 17, 7, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_save_query_and_summary(repo):
    await repo.save_items([
        _item("600110.SH", "limit_up", ladder=3),
        _item("000811.SZ", "limit_up", ladder=1),
        _item("000777.SZ", "broken_limit"),
        _item("603586.SH", "down_limit", ladder=2),
    ])

    rows = await repo.list_by_date("ashare", "2026-06-17", pool_type="limit_up")
    assert [r.symbol for r in rows] == ["600110.SH", "000811.SZ"]

    summary = await repo.summary_by_date("ashare", "2026-06-17")
    assert summary["limit_up_count"] == 2
    assert summary["broken_count"] == 1
    assert summary["down_limit_count"] == 1
    assert summary["break_rate"] == pytest.approx(1 / 3)
    assert summary["max_ladder_height"] == 3
    assert summary["ladder_break_count"] == 1


@pytest.mark.asyncio
async def test_save_items_is_idempotent(repo):
    item = _item("600110.SH", "limit_up", ladder=1)
    await repo.save_items([item])
    await repo.save_items([replace(item, ladder_count=2)])

    rows = await repo.list_by_date("ashare", "2026-06-17")
    assert len(rows) == 1
    assert rows[0].ladder_count == 2
