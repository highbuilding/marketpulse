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
async def test_position_repo_upsert_and_list(repo):
    opened_at = datetime(2026, 6, 7, tzinfo=timezone.utc)
    row_id = await repo.upsert(
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
async def test_position_repo_upsert_is_idempotent(repo):
    await repo.upsert(Position(market="ashare", symbol="002415.SZ", quantity=100))
    first = await repo.get("ashare", "002415.SZ")
    await repo.upsert(Position(market="ashare", symbol="002415.SZ", quantity=200))
    second = await repo.get("ashare", "002415.SZ")

    assert first is not None and second is not None
    assert second.id == first.id
    assert second.quantity == 200


@pytest.mark.asyncio
async def test_position_repo_close_soft_deletes(repo):
    await repo.upsert(Position(market="ashare", symbol="002415.SZ"))
    await repo.close("ashare", "002415.SZ")

    assert await repo.list_by_market("ashare") == []
    closed = await repo.list_by_market("ashare", include_closed=True)
    assert len(closed) == 1
    assert closed[0].status == "closed"
