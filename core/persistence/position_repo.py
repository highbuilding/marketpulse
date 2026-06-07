from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import Position


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


class PositionRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row_to_position(self, row: aiosqlite.Row) -> Position:
        return Position(
            id=row["id"],
            market=row["market"],
            symbol=row["symbol"],
            name=row["name"],
            quantity=row["quantity"],
            cost_price=row["cost_price"],
            opened_at=_dt(row["opened_at"]),
            strategy_tag=row["strategy_tag"],
            entry_reason=row["entry_reason"],
            status=row["status"],
            note=row["note"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    async def upsert(self, position: Position) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO positions (
                     market, symbol, name, quantity, cost_price, opened_at,
                     strategy_tag, entry_reason, status, note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, symbol) DO UPDATE SET
                     name=excluded.name,
                     quantity=excluded.quantity,
                     cost_price=excluded.cost_price,
                     opened_at=excluded.opened_at,
                     strategy_tag=excluded.strategy_tag,
                     entry_reason=excluded.entry_reason,
                     status=excluded.status,
                     note=excluded.note,
                     updated_at=excluded.updated_at""",
                (
                    position.market,
                    position.symbol,
                    position.name,
                    position.quantity,
                    position.cost_price,
                    _iso(position.opened_at),
                    position.strategy_tag,
                    position.entry_reason,
                    position.status,
                    position.note,
                    position.created_at.astimezone(timezone.utc).isoformat()
                    if position.created_at else now,
                    now,
                ),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT id FROM positions WHERE market = ? AND symbol = ?",
                (position.market, position.symbol),
            )
            row = await cur.fetchone()
        return int(row["id"])

    async def get(self, market: str, symbol: str) -> Position | None:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT id, market, symbol, name, quantity, cost_price, opened_at,
                          strategy_tag, entry_reason, status, note, created_at, updated_at
                   FROM positions
                   WHERE market = ? AND symbol = ?""",
                (market, symbol),
            )
            row = await cur.fetchone()
        return self._row_to_position(row) if row else None

    async def list_by_market(
        self, market: str, *, include_closed: bool = False,
    ) -> list[Position]:
        sql = [
            """SELECT id, market, symbol, name, quantity, cost_price, opened_at,
                      strategy_tag, entry_reason, status, note, created_at, updated_at
               FROM positions
               WHERE market = ?""",
        ]
        args: list[object] = [market]
        if not include_closed:
            sql.append("AND status != 'closed'")
        sql.append("ORDER BY updated_at DESC, id DESC")
        async with self._connect() as db:
            cur = await db.execute(" ".join(sql), args)
            rows = await cur.fetchall()
        return [self._row_to_position(r) for r in rows]

    async def close(self, market: str, symbol: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """UPDATE positions
                   SET status = 'closed', updated_at = ?
                   WHERE market = ? AND symbol = ?""",
                (datetime.now(timezone.utc).isoformat(), market, symbol),
            )
            await db.commit()
