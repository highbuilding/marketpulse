from datetime import datetime, timezone

import pytest

from core.domain.models import MarketEvent
from core.persistence.market_event_repo import MarketEventRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return MarketEventRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_market_event_repo_add_and_list_recent(repo):
    occurred_at = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)
    event_id = await repo.add(
        MarketEvent(
            market="ashare",
            event_type="theme_divergence",
            severity="warning",
            subject_type="theme",
            subject_id="BK001",
            title="机器人板块分歧扩大",
            summary="后排回落, 核心仍有承接",
            evidence={"up_ratio": 0.42},
            occurred_at=occurred_at,
        ),
    )

    rows = await repo.list_recent("ashare", subject_type="theme", subject_id="BK001")
    assert event_id > 0
    assert len(rows) == 1
    assert rows[0].title == "机器人板块分歧扩大"
    assert rows[0].occurred_at == occurred_at
    assert rows[0].evidence == {"up_ratio": 0.42}
