from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.fund_flow_service import FundFlowService


_SYMBOL_FLOW_DF = pd.DataFrame([
    {"日期": "2026-05-13",
     "主力净流入-净额": 10_000_000, "超大单净流入-净额": 5_000_000,
     "大单净流入-净额": 3_000_000, "中单净流入-净额": 1_500_000,
     "小单净流入-净额": 500_000},
])

_NORTH_DF = pd.DataFrame([
    {"日期": "2026-05-13", "当日成交净买额": 8e8, "当日资金流入": 5e8,
     "当日余额": 2e9, "历史累计净买额": 1e12},
])


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return FundFlowService(FundFlowRepo(str(tmp_path / "state.db")))


@pytest.mark.asyncio
async def test_pull_symbol_flow(svc):
    with patch("core.services.fund_flow_service.ak.stock_individual_fund_flow",
               return_value=_SYMBOL_FLOW_DF):
        n = await svc.pull_symbol_flow("600519.SH")
    assert n == 1
    rows = await svc.query_symbol("600519.SH",
                                   start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                                   end=datetime(2026, 5, 14, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0].main_net == pytest.approx(1e7)


@pytest.mark.asyncio
async def test_pull_north_flow(svc):
    with patch("core.services.fund_flow_service.ak.stock_hsgt_hist_em",
               return_value=_NORTH_DF):
        await svc.pull_north_flow()
    rows = await svc.query_north(
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].hgt_net is not None
