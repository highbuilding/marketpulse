from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.domain.models import ThemeSnapshot
from core.persistence.sqlite_repo import StateRepo
from core.persistence.theme_repo import ThemeRepo


def _snap(ts: datetime, *, theme: str = "theme:ai", up: float = 0.5) -> ThemeSnapshot:
    return ThemeSnapshot(
        market="ashare",
        theme_code=theme,
        theme_name="人工智能",
        classification="theme",
        ts=ts,
        pct_change=1.2,
        pct_change_5m=0.3,
        amount=1e8,
        amount_ratio=1.1,
        up_ratio=up,
        limit_up_count=2,
        member_count=10,
        leader_symbols=["600000.SH"],
        divergence_score=0.1,
        support_score=0.2,
        raw={},
    )


@pytest.mark.asyncio
async def test_list_snapshots_window_filters_and_sorts_asc(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = ThemeRepo(str(db))

    t0930 = datetime(2026, 6, 15, 1, 30, tzinfo=timezone.utc)  # 09:30 BJT
    t1000 = datetime(2026, 6, 15, 2, 0, tzinfo=timezone.utc)   # 10:00 BJT
    t_prev = datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc)  # 前一日, 窗口外

    # 故意乱序插入
    await repo.upsert_snapshots([_snap(t1000, up=0.7), _snap(t_prev, up=0.3), _snap(t0930, up=0.5)])

    start = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 15, 15, 59, 59, tzinfo=timezone.utc)
    rows = await repo.list_snapshots_window("ashare", start=start, end=end)

    # 窗口外的前一日快照被排除
    assert len(rows) == 2
    # 升序: 09:30 在 10:00 之前
    assert rows[0].ts == t0930
    assert rows[1].ts == t1000
    assert rows[0].up_ratio == 0.5
    assert rows[1].up_ratio == 0.7


@pytest.mark.asyncio
async def test_list_snapshots_window_empty_when_no_data(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = ThemeRepo(str(db))
    start = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 15, 23, 59, tzinfo=timezone.utc)
    assert await repo.list_snapshots_window("ashare", start=start, end=end) == []
