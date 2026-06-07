from datetime import datetime, timedelta, timezone

import pytest

from core.domain.models import AITradeOpinion
from core.persistence.ai_opinion_repo import AITradeOpinionRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return AITradeOpinionRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_ai_opinion_repo_upsert_and_get(repo):
    generated_at = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)
    expires_at = generated_at + timedelta(minutes=30)
    opinion_id = await repo.upsert(
        AITradeOpinion(
            market="ashare",
            opinion_key="theme:BK001:20260607T1000",
            target_type="theme",
            target_id="BK001",
            target_name="机器人",
            decision="wait_pullback",
            confidence=0.72,
            title="机器人弱分歧, 等核心回踩",
            thesis="主线仍在, 但后排分化扩大。",
            reasons=["核心股承接", "成交额保持"],
            risks=["后排回落"],
            evidence={"state": "DIVERGING"},
            generated_at=generated_at,
            expires_at=expires_at,
        ),
    )

    row = await repo.get(opinion_id)
    assert row is not None
    assert row.generated_at == generated_at
    assert row.expires_at == expires_at
    assert row.reasons == ["核心股承接", "成交额保持"]
    assert row.evidence == {"state": "DIVERGING"}


@pytest.mark.asyncio
async def test_ai_opinion_repo_upsert_is_idempotent(repo):
    await repo.upsert(
        AITradeOpinion(
            market="ashare",
            opinion_key="symbol:002415.SZ:hold",
            target_type="symbol",
            target_id="002415.SZ",
            target_name="海康威视",
            decision="hold",
            confidence=0.62,
            title="继续持有观察",
            thesis="分时未破位。",
            reasons=["仍在均线上方"],
            risks=[],
            evidence={},
        ),
    )
    await repo.upsert(
        AITradeOpinion(
            market="ashare",
            opinion_key="symbol:002415.SZ:hold",
            target_type="symbol",
            target_id="002415.SZ",
            target_name="海康威视",
            decision="reduce_watch",
            confidence=0.55,
            title="减仓观察",
            thesis="承接转弱。",
            reasons=["跌破分时均线"],
            risks=["题材退潮"],
            evidence={"event": "break_intraday_avg"},
        ),
    )

    rows = await repo.list_active("ashare")
    assert len(rows) == 1
    assert rows[0].decision == "reduce_watch"
    assert rows[0].risks == ["题材退潮"]
    assert rows[0].evidence == {"event": "break_intraday_avg"}
