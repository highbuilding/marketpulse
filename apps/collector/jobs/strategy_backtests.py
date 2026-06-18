from __future__ import annotations

import structlog

from core.services.strategy_backtest_service import StrategyBacktestService

log = structlog.get_logger(__name__)


async def refresh_ashare_strategy_backtests(service: StrategyBacktestService) -> None:
    try:
        runs = await service.run_all(market="ashare", lookback_days=720, max_symbols=120)
        log.info(
            "strategy_backtests.refresh_done",
            market="ashare",
            strategies=len(runs),
            passed=sum(1 for r in runs if r.sandbox_eligible),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("strategy_backtests.refresh_failed", market="ashare", error=str(e))
