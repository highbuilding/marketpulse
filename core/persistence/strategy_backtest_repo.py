from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import StrategyBacktestRun, StrategyBacktestTrade, TradeInstruction


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


class StrategyBacktestRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row_to_run(self, row: aiosqlite.Row) -> StrategyBacktestRun:
        return StrategyBacktestRun(
            id=row["id"],
            market=row["market"],
            strategy_id=row["strategy_id"],
            strategy_name=row["strategy_name"],
            description=row["description"],
            horizon=row["horizon"],
            status=row["status"],
            engine=row["engine"],
            sample_start=row["sample_start"],
            sample_end=row["sample_end"],
            symbol_count=int(row["symbol_count"] or 0),
            trade_count=int(row["trade_count"] or 0),
            win_rate=row["win_rate"],
            avg_return_pct=row["avg_return_pct"],
            median_return_pct=row["median_return_pct"],
            max_drawdown_pct=row["max_drawdown_pct"],
            worst_trade_pct=row["worst_trade_pct"],
            avg_holding_days=row["avg_holding_days"],
            annual_return_pct=row["annual_return_pct"],
            total_return_pct=row["total_return_pct"],
            sandbox_eligible=bool(row["sandbox_eligible"]),
            metrics=_loads(row["metrics_json"], {}),
            returns=_loads(row["returns_json"], {}),
            yearly=_loads(row["yearly_json"], []),
            market_state=_loads(row["market_state_json"], []),
            theme_state=_loads(row["theme_state_json"], []),
            exit_comparison=_loads(row["exit_comparison_json"], []),
            equity_curve=_loads(row["equity_curve_json"], []),
            data_gaps=_loads(row["data_gaps_json"], []),
            generated_at=_dt(row["generated_at"]),
        )

    def _row_to_trade(self, row: aiosqlite.Row) -> StrategyBacktestTrade:
        return StrategyBacktestTrade(
            id=row["id"],
            run_id=row["run_id"],
            market=row["market"],
            strategy_id=row["strategy_id"],
            symbol=row["symbol"],
            name=row["name"],
            entry_date=row["entry_date"],
            exit_date=row["exit_date"],
            entry_price=float(row["entry_price"]),
            exit_price=float(row["exit_price"]),
            return_pct=float(row["return_pct"]),
            holding_days=int(row["holding_days"]),
            exit_reason=row["exit_reason"],
            evidence=_loads(row["evidence_json"], {}),
        )

    def _row_to_instruction(self, row: aiosqlite.Row) -> TradeInstruction:
        evidence = _loads(row["evidence_json"], {})
        if row["strategy_id"] is not None:
            evidence = {**evidence, "strategy_id": row["strategy_id"],
                        "strategy_name": row["strategy_name"]}
        return TradeInstruction(
            id=row["id"],
            market=row["market"],
            instruction_key=row["instruction_key"],
            action=row["action"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            target_name=row["target_name"],
            candidate_key=row["candidate_key"],
            title=row["title"],
            summary=row["summary"],
            confidence=float(row["confidence"]),
            severity=row["severity"],
            reasons=_loads(row["reasons_json"], []),
            risks=_loads(row["risks_json"], []),
            entry_conditions=_loads(row["entry_conditions_json"], []),
            invalidation_conditions=_loads(row["invalidation_conditions_json"], []),
            evidence=evidence,
            generated_at=_dt(row["generated_at"]),
            expires_at=_dt(row["expires_at"]),
            status=row["status"],
        )

    async def save_run(
        self, run: StrategyBacktestRun, trades: list[StrategyBacktestTrade],
    ) -> int:
        now = (run.generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO strategy_backtest_runs (
                     market, strategy_id, strategy_name, description, horizon, status, engine,
                     sample_start, sample_end, symbol_count, trade_count, win_rate,
                     avg_return_pct, median_return_pct, max_drawdown_pct, worst_trade_pct,
                     avg_holding_days, annual_return_pct, total_return_pct, sandbox_eligible,
                     metrics_json, returns_json, yearly_json, market_state_json,
                     theme_state_json, exit_comparison_json, equity_curve_json,
                     data_gaps_json, generated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, strategy_id) DO UPDATE SET
                     strategy_name=excluded.strategy_name,
                     description=excluded.description,
                     horizon=excluded.horizon,
                     status=excluded.status,
                     engine=excluded.engine,
                     sample_start=excluded.sample_start,
                     sample_end=excluded.sample_end,
                     symbol_count=excluded.symbol_count,
                     trade_count=excluded.trade_count,
                     win_rate=excluded.win_rate,
                     avg_return_pct=excluded.avg_return_pct,
                     median_return_pct=excluded.median_return_pct,
                     max_drawdown_pct=excluded.max_drawdown_pct,
                     worst_trade_pct=excluded.worst_trade_pct,
                     avg_holding_days=excluded.avg_holding_days,
                     annual_return_pct=excluded.annual_return_pct,
                     total_return_pct=excluded.total_return_pct,
                     sandbox_eligible=excluded.sandbox_eligible,
                     metrics_json=excluded.metrics_json,
                     returns_json=excluded.returns_json,
                     yearly_json=excluded.yearly_json,
                     market_state_json=excluded.market_state_json,
                     theme_state_json=excluded.theme_state_json,
                     exit_comparison_json=excluded.exit_comparison_json,
                     equity_curve_json=excluded.equity_curve_json,
                     data_gaps_json=excluded.data_gaps_json,
                     generated_at=excluded.generated_at""",
                (
                    run.market, run.strategy_id, run.strategy_name, run.description,
                    run.horizon, run.status, run.engine, run.sample_start, run.sample_end,
                    run.symbol_count, run.trade_count, run.win_rate, run.avg_return_pct,
                    run.median_return_pct, run.max_drawdown_pct, run.worst_trade_pct,
                    run.avg_holding_days, run.annual_return_pct, run.total_return_pct,
                    int(run.sandbox_eligible), _json(run.metrics, {}),
                    _json(run.returns, {}), _json(run.yearly, []),
                    _json(run.market_state, []), _json(run.theme_state, []),
                    _json(run.exit_comparison, []), _json(run.equity_curve, []),
                    _json(run.data_gaps, []), now,
                ),
            )
            cur = await db.execute(
                "SELECT id FROM strategy_backtest_runs WHERE market=? AND strategy_id=?",
                (run.market, run.strategy_id),
            )
            row = await cur.fetchone()
            run_id = int(row["id"])
            await db.execute("DELETE FROM strategy_backtest_trades WHERE run_id=?", (run_id,))
            if trades:
                await db.executemany(
                    """INSERT INTO strategy_backtest_trades (
                         run_id, market, strategy_id, symbol, name, entry_date, exit_date,
                         entry_price, exit_price, return_pct, holding_days, exit_reason,
                         evidence_json
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id, t.market, t.strategy_id, t.symbol, t.name, t.entry_date,
                            t.exit_date, t.entry_price, t.exit_price, t.return_pct,
                            t.holding_days, t.exit_reason, _json(t.evidence, {}),
                        )
                        for t in trades
                    ],
                )
            await db.commit()
        return run_id

    async def list_latest(self, market: str) -> list[StrategyBacktestRun]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT * FROM strategy_backtest_runs
                   WHERE market = ?
                   ORDER BY sandbox_eligible DESC, trade_count DESC, strategy_id ASC""",
                (market,),
            )
            rows = await cur.fetchall()
        return [self._row_to_run(r) for r in rows]

    async def get_latest(self, market: str, strategy_id: str) -> StrategyBacktestRun | None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT * FROM strategy_backtest_runs WHERE market=? AND strategy_id=?",
                (market, strategy_id),
            )
            row = await cur.fetchone()
        return self._row_to_run(row) if row else None

    async def list_trades(self, run_id: int, *, limit: int = 200) -> list[StrategyBacktestTrade]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT * FROM strategy_backtest_trades
                   WHERE run_id = ?
                   ORDER BY entry_date DESC, return_pct ASC
                   LIMIT ?""",
                (run_id, limit),
            )
            rows = await cur.fetchall()
        return [self._row_to_trade(r) for r in rows]

    async def upsert_instructions(self, instructions: list[TradeInstruction]) -> int:
        if not instructions:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for item in instructions:
            evidence = item.evidence or {}
            rows.append((
                item.market, item.instruction_key, item.action, item.target_type,
                item.target_id, item.target_name, evidence.get("strategy_id"),
                evidence.get("strategy_name"), item.candidate_key, item.title, item.summary,
                item.confidence, item.severity, _json(item.reasons, []),
                _json(item.risks, []), _json(item.entry_conditions, []),
                _json(item.invalidation_conditions, []), _json(evidence, {}),
                (item.generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                item.expires_at.astimezone(timezone.utc).isoformat() if item.expires_at else None,
                item.status,
            ))
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO trade_instructions (
                     market, instruction_key, action, target_type, target_id, target_name,
                     strategy_id, strategy_name, candidate_key, title, summary, confidence,
                     severity, reasons_json, risks_json, entry_conditions_json,
                     invalidation_conditions_json, evidence_json, generated_at, expires_at, status
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, instruction_key) DO UPDATE SET
                     action=excluded.action,
                     target_type=excluded.target_type,
                     target_id=excluded.target_id,
                     target_name=excluded.target_name,
                     strategy_id=excluded.strategy_id,
                     strategy_name=excluded.strategy_name,
                     candidate_key=excluded.candidate_key,
                     title=excluded.title,
                     summary=excluded.summary,
                     confidence=excluded.confidence,
                     severity=excluded.severity,
                     reasons_json=excluded.reasons_json,
                     risks_json=excluded.risks_json,
                     entry_conditions_json=excluded.entry_conditions_json,
                     invalidation_conditions_json=excluded.invalidation_conditions_json,
                     evidence_json=excluded.evidence_json,
                     generated_at=excluded.generated_at,
                     expires_at=excluded.expires_at,
                     status=excluded.status""",
                rows,
            )
            await db.commit()
            return db.total_changes - before

    async def deactivate_active_instructions(self, market: str) -> int:
        async with self._connect() as db:
            before = db.total_changes
            await db.execute(
                """UPDATE trade_instructions
                   SET status = 'expired'
                   WHERE market = ? AND status = 'active'""",
                (market,),
            )
            await db.commit()
            return db.total_changes - before

    async def list_instructions(
        self, market: str, *, status: str = "active", limit: int = 100,
    ) -> list[TradeInstruction]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT * FROM trade_instructions
                   WHERE market = ? AND status = ?
                   ORDER BY severity DESC, confidence DESC, generated_at DESC
                   LIMIT ?""",
                (market, status, limit),
            )
            rows = await cur.fetchall()
        return [self._row_to_instruction(r) for r in rows]
