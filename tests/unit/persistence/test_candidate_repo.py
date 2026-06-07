import pytest

from core.domain.models import TradeCandidate
from core.persistence.candidate_repo import CandidateRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return CandidateRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_candidate_repo_upsert_is_idempotent(repo):
    first_id = await repo.upsert(
        TradeCandidate(
            market="ashare",
            candidate_key="ashare:002415.SZ:open_watch",
            symbol="002415.SZ",
            name="海康威视",
            theme_code="BK001",
            theme_name="机器人",
            candidate_type="theme_core_pullback",
            decision="open_watch",
            score=72.0,
            reasons=["题材扩散", "核心股承接"],
            risks=["后排分化"],
            evidence={"state": "DIVERGING"},
        ),
    )
    second_id = await repo.upsert(
        TradeCandidate(
            market="ashare",
            candidate_key="ashare:002415.SZ:open_watch",
            symbol="002415.SZ",
            name="海康威视",
            theme_code="BK001",
            theme_name="机器人",
            candidate_type="theme_core_pullback",
            decision="open_watch",
            score=83.0,
            reasons=["承接增强"],
            risks=["高开追涨风险"],
            evidence={"state": "REPAIRING"},
        ),
    )

    rows = await repo.list_active("ashare")
    assert second_id == first_id
    assert len(rows) == 1
    assert rows[0].score == pytest.approx(83.0)
    assert rows[0].reasons == ["承接增强"]
    assert rows[0].evidence == {"state": "REPAIRING"}


@pytest.mark.asyncio
async def test_candidate_repo_set_status_hides_from_active(repo):
    await repo.upsert(
        TradeCandidate(
            market="ashare",
            candidate_key="k1",
            symbol="002415.SZ",
            candidate_type="low_start",
            decision="observe_only",
            score=50.0,
        ),
    )
    await repo.set_status("ashare", "k1", "expired")
    assert await repo.list_active("ashare") == []
