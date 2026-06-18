from __future__ import annotations

import pytest

from core.persistence.candidate_repo import CandidateRepo
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.duckdb_repo import BarRepo
from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.signal_repo import SignalRepo
from core.persistence.sqlite_repo import StateRepo
from core.persistence.strategy_backtest_repo import StrategyBacktestRepo
from core.persistence.theme_repo import ThemeRepo
from core.services.strategy_backtest_service import StrategyBacktestService


@pytest.mark.asyncio
async def test_strategy_backtest_service_saves_insufficient_runs_without_bars(tmp_path):
    state = tmp_path / "state.db"
    bars = tmp_path / "bars.duckdb"
    await StateRepo(str(state)).init()
    bar_repo = BarRepo(str(bars))
    bar_repo.init()

    service = StrategyBacktestService(
        StrategyBacktestRepo(str(state)),
        bar_repo=bar_repo,
        signals=SignalRepo(str(state)),
        candidates=CandidateRepo(str(state)),
        collector_symbols=CollectorSymbolRepo(str(state)),
        limit_pool=LimitPoolRepo(str(state)),
        themes=ThemeRepo(str(state)),
    )

    runs = await service.run_all(max_symbols=5, lookback_days=30)

    assert len(runs) == 6
    assert {r.status for r in runs} == {"insufficient_data"}
    assert all("bars_1d_missing" in (r.data_gaps or []) for r in runs)
