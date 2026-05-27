from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from core.persistence.chip_repo import ChipRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.chip_service import ChipService


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return ChipService(ChipRepo(str(tmp_path / "state.db")))


@pytest.mark.asyncio
async def test_pull_summary_parses_stock_cyq_em(svc):
    df = pd.DataFrame([{
        "日期": "2026-05-20",
        "获利比例": 0.61,
        "平均成本": 38.5,
        "90成本-低": 30.1,
        "90成本-高": 45.2,
        "90集中度": 0.21,
        "70成本-低": 34.0,
        "70成本-高": 41.1,
        "70集中度": 0.12,
    }])
    with patch("core.services.chip_service.ak_call", AsyncMock(return_value=df)) as mocked:
        rows = await svc.pull_summary("002415.SZ")
    mocked.assert_awaited_once()
    assert len(rows) == 1
    assert rows[0].avg_cost == pytest.approx(38.5)
    cached = await svc.get_summary("002415.SZ")
    assert cached[0].profit_ratio == pytest.approx(0.61)


@pytest.mark.asyncio
async def test_non_ashare_returns_empty_without_ak_call(svc):
    with patch("core.services.chip_service.ak_call", AsyncMock()) as mocked:
        rows = await svc.get_summary("AAPL")
    mocked.assert_not_called()
    assert rows == []
