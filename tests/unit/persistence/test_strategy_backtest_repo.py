from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.domain.models import StrategyBacktestRun, StrategyBacktestTrade, TradeInstruction
from core.persistence.sqlite_repo import StateRepo
from core.persistence.strategy_backtest_repo import StrategyBacktestRepo


@pytest.mark.asyncio
async def test_strategy_backtest_repo_roundtrip(tmp_path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = StrategyBacktestRepo(str(db))

    run = StrategyBacktestRun(
        market="ashare",
        strategy_id="low_position_breakout",
        strategy_name="低位容量趋势 + 放量突破",
        description="test",
        horizon="3-10日波段",
        status="passed",
        engine="vectorbt",
        sample_start="2026-01-01",
        sample_end="2026-06-01",
        symbol_count=2,
        trade_count=1,
        win_rate=100,
        avg_return_pct=3.2,
        max_drawdown_pct=-2.1,
        sandbox_eligible=True,
        returns={"5d": {"count": 1, "avg_return_pct": 3.2}},
        data_gaps=[],
        generated_at=datetime.now(timezone.utc),
    )
    trade = StrategyBacktestTrade(
        market="ashare",
        strategy_id=run.strategy_id,
        symbol="000001.SZ",
        entry_date="2026-01-02",
        exit_date="2026-01-07",
        entry_price=10,
        exit_price=11,
        return_pct=9.85,
        holding_days=5,
        exit_reason="time_stop",
    )

    run_id = await repo.save_run(run, [trade])
    rows = await repo.list_latest("ashare")
    trades = await repo.list_trades(run_id)

    assert rows[0].strategy_id == "low_position_breakout"
    assert rows[0].sandbox_eligible is True
    assert rows[0].returns["5d"]["count"] == 1
    assert trades[0].symbol == "000001.SZ"


@pytest.mark.asyncio
async def test_trade_instruction_roundtrip(tmp_path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = StrategyBacktestRepo(str(db))

    await repo.upsert_instructions([
        TradeInstruction(
            market="ashare",
            instruction_key="s:c",
            action="BUY_SETUP",
            target_type="symbol",
            target_id="000001.SZ",
            title="进入纸面观察",
            summary="历史回测通过",
            confidence=0.58,
            severity="watch",
            evidence={"strategy_id": "s", "strategy_name": "策略"},
        )
    ])
    rows = await repo.list_instructions("ashare")

    assert rows[0].action == "BUY_SETUP"
    assert rows[0].evidence["strategy_id"] == "s"

    changed = await repo.deactivate_active_instructions("ashare")
    rows = await repo.list_instructions("ashare")

    assert changed == 1
    assert rows == []
