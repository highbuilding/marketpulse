from datetime import datetime, timezone

import pytest

from core.domain.models import Position
from core.persistence.position_repo import PositionRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return PositionRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_position_repo_create_and_list(repo):
    opened_at = datetime(2026, 6, 7, tzinfo=timezone.utc)
    row_id = await repo.create(
        Position(
            market="ashare",
            symbol="002415.SZ",
            name="海康威视",
            quantity=100,
            cost_price=31.2,
            opened_at=opened_at,
            strategy_tag="低位启动",
            entry_reason="机器人题材扩散",
            note="观察分时承接",
        ),
    )

    assert row_id > 0
    rows = await repo.list_by_market("ashare")
    assert len(rows) == 1
    assert rows[0].symbol == "002415.SZ"
    assert rows[0].opened_at == opened_at
    assert rows[0].entry_reason == "机器人题材扩散"


@pytest.mark.asyncio
async def test_position_repo_create_allows_multiple_same_symbol(repo):
    # A 方案: 同标的可多条记录(多次开/平仓), 无 UNIQUE 约束
    id1 = await repo.create(Position(market="ashare", symbol="002415.SZ", quantity=100))
    id2 = await repo.create(Position(market="ashare", symbol="002415.SZ", quantity=200))
    assert id1 != id2
    rows = await repo.list_by_market("ashare")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_position_repo_update_by_id(repo):
    row_id = await repo.create(Position(market="ashare", symbol="002415.SZ", quantity=100))
    await repo.update(Position(id=row_id, market="ashare", symbol="002415.SZ", quantity=200))
    got = await repo.get_by_id(row_id)
    assert got is not None
    assert got.quantity == 200


@pytest.mark.asyncio
async def test_position_repo_close_records_profit(repo):
    row_id = await repo.create(
        Position(market="ashare", symbol="002415.SZ", quantity=100, cost_price=30.0),
    )
    await repo.close(row_id, close_price=33.0, profit_amount=300.0, profit_pct=10.0)

    assert await repo.list_by_market("ashare") == []
    closed = await repo.list_by_market("ashare", include_closed=True)
    assert len(closed) == 1
    assert closed[0].status == "closed"
    assert closed[0].close_price == 33.0
    assert closed[0].profit_amount == 300.0
    assert closed[0].profit_pct == 10.0
    assert closed[0].closed_at is not None


@pytest.mark.asyncio
async def test_position_repo_delete(repo):
    row_id = await repo.create(Position(market="ashare", symbol="002415.SZ"))
    await repo.delete(row_id)
    assert await repo.list_by_market("ashare", include_closed=True) == []
