from datetime import datetime, timezone

import pytest

from core.domain.models import ThemeMembership, ThemeSnapshot, ThemeState
from core.persistence.sqlite_repo import StateRepo
from core.persistence.theme_repo import ThemeRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return ThemeRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_theme_repo_upsert_snapshot_replaces_same_ts(repo):
    ts = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)
    await repo.upsert_snapshots([
        ThemeSnapshot(
            market="ashare",
            theme_code="BK001",
            theme_name="机器人",
            classification="concept",
            ts=ts,
            pct_change=1.2,
            leader_symbols=["300024.SZ"],
            raw={"source": "test"},
        ),
    ])
    await repo.upsert_snapshots([
        ThemeSnapshot(
            market="ashare",
            theme_code="BK001",
            theme_name="机器人",
            classification="concept",
            ts=ts,
            pct_change=2.3,
            leader_symbols=["002415.SZ"],
            raw={"source": "test2"},
        ),
    ])

    rows = await repo.list_recent_snapshots("ashare")
    assert len(rows) == 1
    assert rows[0].pct_change == pytest.approx(2.3)
    assert rows[0].leader_symbols == ["002415.SZ"]
    assert rows[0].raw == {"source": "test2"}


@pytest.mark.asyncio
async def test_theme_repo_state_and_membership_json_roundtrip(repo):
    await repo.upsert_states([
        ThemeState(
            market="ashare",
            theme_code="BK001",
            theme_name="机器人",
            state="DIVERGING",
            score=78.5,
            reason="核心强, 后排分化",
            evidence={"leader": "002415.SZ"},
        ),
    ])
    await repo.upsert_memberships([
        ThemeMembership(
            market="ashare",
            theme_code="BK001",
            symbol="002415.SZ",
            name="海康威视",
            role="core",
            pct_change=3.2,
            amount=12_000_000,
            volume_ratio=1.8,
            is_above_intraday_avg=True,
            evidence={"rank": 2},
        ),
    ])

    states = await repo.list_states("ashare")
    members = await repo.list_memberships("ashare", "BK001")
    assert states[0].state == "DIVERGING"
    assert states[0].evidence == {"leader": "002415.SZ"}
    assert members[0].role == "core"
    assert members[0].is_above_intraday_avg is True
    assert members[0].evidence == {"rank": 2}
