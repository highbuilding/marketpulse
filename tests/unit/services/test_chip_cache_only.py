from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.models import ChipSummary
from core.services.chip_service import ChipService


def make_summary(date_str: str) -> ChipSummary:
    return ChipSummary(
        symbol="600519.SH",
        trade_date=datetime.fromisoformat(date_str),
        profit_ratio=0.7, avg_cost=1500.0,
        cost_90_low=1400.0, cost_90_high=1600.0, concentration_90=0.1,
        cost_70_low=1450.0, cost_70_high=1550.0, concentration_70=0.05,
    )


async def test_cache_only_returns_db_rows_no_ak():
    repo = MagicMock()
    repo.list_recent = AsyncMock(return_value=[
        make_summary("2026-05-01T00:00:00+00:00"),
        make_summary("2026-05-02T00:00:00+00:00"),
    ])
    svc = ChipService(repo=repo)
    rows = await svc.get_summary_cache_only("600519.SH", days=90)
    assert len(rows) == 2
    repo.list_recent.assert_awaited_once_with("600519.SH", limit=90)


async def test_cache_only_returns_empty_no_db():
    repo = MagicMock()
    repo.list_recent = AsyncMock(return_value=[])
    svc = ChipService(repo=repo)
    rows = await svc.get_summary_cache_only("600519.SH", days=90)
    assert rows == []


async def test_cache_only_skips_non_ashare():
    repo = MagicMock()
    repo.list_recent = AsyncMock(return_value=[])
    svc = ChipService(repo=repo)
    rows = await svc.get_summary_cache_only("00700.HK", days=90)
    assert rows == []
    # 不应触达 repo
    repo.list_recent.assert_not_called()
