from datetime import datetime, timezone

import pytest

from core.persistence.sqlite_repo import StateRepo


@pytest.mark.asyncio
async def test_init_creates_tables(tmp_path):
    repo = StateRepo(str(tmp_path / "test.db"))
    await repo.init()
    async with repo.connect() as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in await cur.fetchall()}
    assert "health_log" in names and "app_state" in names
    assert "positions" in names
    assert "theme_snapshots" in names
    assert "theme_states" in names
    assert "theme_memberships" in names
    assert "market_events" in names
    assert "trade_candidates" in names
    assert "ai_trade_opinions" in names


@pytest.mark.asyncio
async def test_record_and_recent_health(tmp_path):
    repo = StateRepo(str(tmp_path / "test.db"))
    await repo.init()
    await repo.record_health("ashare", "ok", None, ts=datetime(2026, 5, 13, tzinfo=timezone.utc))
    await repo.record_health("us", "disabled", "missing key",
                             ts=datetime(2026, 5, 13, tzinfo=timezone.utc))
    rows = await repo.recent_health(limit=10)
    assert len(rows) == 2
    assert {r["component"] for r in rows} == {"ashare", "us"}


@pytest.mark.asyncio
async def test_state_get_set(tmp_path):
    repo = StateRepo(str(tmp_path / "test.db"))
    await repo.init()
    await repo.set_state("last_warmup", "2026-05-13T10:00:00Z")
    assert await repo.get_state("last_warmup") == "2026-05-13T10:00:00Z"
    assert await repo.get_state("missing") is None
