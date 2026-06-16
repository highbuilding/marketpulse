from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.domain.models import LiveMessage
from core.persistence.live_message_repo import LiveMessageRepo
from core.persistence.sqlite_repo import StateRepo


def _msg(msg_id: str, ts: datetime) -> LiveMessage:
    return LiveMessage(
        id=msg_id,
        market="ashare",
        ts=ts,
        level="watch",
        category="theme",
        title="AI算力走强",
        body="5只成分股中4只上涨",
        source_event="bus:quote.tick",
        dedupe_key="theme:theme:ai_compute:strength",
        theme_code="theme:ai_compute",
        symbol="300308.SZ",
        symbols=["300308.SZ", "300502.SZ"],
        payload={"up_count": 4},
    )


@pytest.mark.asyncio
async def test_live_message_insert_is_idempotent_and_queryable(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = LiveMessageRepo(str(db))
    now = datetime.now(timezone.utc)

    assert await repo.insert_many([_msg("m1", now)]) == 1
    assert await repo.insert_many([_msg("m1", now)]) == 0

    recent = await repo.list_recent("ashare", limit=10)
    assert len(recent) == 1
    assert recent[0].payload == {"up_count": 4}
    assert recent[0].symbols == ["300308.SZ", "300502.SZ"]

    window = await repo.list_window(
        "ashare",
        start=now - timedelta(minutes=1),
        end=now + timedelta(minutes=1),
    )
    assert [m.id for m in window] == ["m1"]


@pytest.mark.asyncio
async def test_live_message_window_asc_preserves_early_messages(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = LiveMessageRepo(str(db))
    start = datetime(2026, 6, 16, 1, 30, tzinfo=timezone.utc)

    await repo.insert_many([
        _msg("m1", start),
        _msg("m2", start + timedelta(minutes=1)),
        _msg("m3", start + timedelta(minutes=2)),
    ])

    rows = await repo.list_window_asc(
        "ashare",
        start=start - timedelta(minutes=1),
        end=start + timedelta(minutes=10),
        limit=2,
    )

    assert [m.id for m in rows] == ["m1", "m2"]

    next_rows = await repo.list_window_asc(
        "ashare",
        start=start - timedelta(minutes=1),
        end=start + timedelta(minutes=10),
        limit=2,
        offset=2,
    )

    assert [m.id for m in next_rows] == ["m3"]
    assert await repo.count_window(
        "ashare",
        start=start - timedelta(minutes=1),
        end=start + timedelta(minutes=10),
    ) == 3
