from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import ChipSummary


class ChipRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def upsert_many(self, rows: list[ChipSummary]) -> int:
        if not rows:
            return 0
        updated_at = datetime.now(timezone.utc).isoformat()
        payload = [
            (
                r.symbol,
                r.trade_date.astimezone(timezone.utc).isoformat(),
                r.profit_ratio,
                r.avg_cost,
                r.cost_90_low,
                r.cost_90_high,
                r.concentration_90,
                r.cost_70_low,
                r.cost_70_high,
                r.concentration_70,
                updated_at,
            )
            for r in rows
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO chip_summary (
                     symbol, trade_date, profit_ratio, avg_cost,
                     cost_90_low, cost_90_high, concentration_90,
                     cost_70_low, cost_70_high, concentration_70, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, trade_date) DO UPDATE SET
                     profit_ratio=excluded.profit_ratio,
                     avg_cost=excluded.avg_cost,
                     cost_90_low=excluded.cost_90_low,
                     cost_90_high=excluded.cost_90_high,
                     concentration_90=excluded.concentration_90,
                     cost_70_low=excluded.cost_70_low,
                     cost_70_high=excluded.cost_70_high,
                     concentration_70=excluded.concentration_70,
                     updated_at=excluded.updated_at""",
                payload,
            )
            await db.commit()
        return len(rows)

    async def list_recent(self, symbol: str, limit: int = 90) -> list[ChipSummary]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT symbol, trade_date, profit_ratio, avg_cost,
                          cost_90_low, cost_90_high, concentration_90,
                          cost_70_low, cost_70_high, concentration_70
                   FROM chip_summary
                   WHERE symbol = ?
                   ORDER BY trade_date DESC
                   LIMIT ?""",
                (symbol, limit),
            )
            rows = await cur.fetchall()
        out = [
            ChipSummary(
                symbol=r["symbol"],
                trade_date=datetime.fromisoformat(r["trade_date"]).astimezone(timezone.utc),
                profit_ratio=r["profit_ratio"],
                avg_cost=r["avg_cost"],
                cost_90_low=r["cost_90_low"],
                cost_90_high=r["cost_90_high"],
                concentration_90=r["concentration_90"],
                cost_70_low=r["cost_70_low"],
                cost_70_high=r["cost_70_high"],
                concentration_70=r["concentration_70"],
            )
            for r in rows
        ]
        return list(reversed(out))
