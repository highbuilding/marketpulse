from datetime import datetime, timezone

import pytest

from core.domain.models import ChipSummary
from core.persistence.chip_repo import ChipRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return ChipRepo(str(tmp_path / "state.db"))


def _row(avg_cost: float) -> ChipSummary:
    return ChipSummary(
        symbol="002415.SZ",
        trade_date=datetime(2026, 5, 20, tzinfo=timezone.utc),
        profit_ratio=0.55,
        avg_cost=avg_cost,
        cost_90_low=30,
        cost_90_high=45,
        concentration_90=0.18,
        cost_70_low=33,
        cost_70_high=41,
        concentration_70=0.11,
    )


@pytest.mark.asyncio
async def test_chip_repo_upsert_replaces_same_day(repo):
    await repo.upsert_many([_row(38.1)])
    await repo.upsert_many([_row(39.2)])
    rows = await repo.list_recent("002415.SZ", limit=10)
    assert len(rows) == 1
    assert rows[0].avg_cost == pytest.approx(39.2)
