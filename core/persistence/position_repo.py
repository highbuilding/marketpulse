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
            close_price=row["close_price"],
            profit_amount=row["profit_amount"],
            profit_pct=row["profit_pct"],
            opened_at=_dt(row["opened_at"]),
            closed_at=_dt(row["closed_at"]),
            strategy_tag=row["strategy_tag"],
            entry_reason=row["entry_reason"],
            status=row["status"],
            note=row["note"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    _COLS = (
        "id, market, symbol, name, quantity, cost_price, close_price, "
        "profit_amount, profit_pct, opened_at, closed_at, strategy_tag, "
        "entry_reason, status, note, created_at, updated_at"
    )

    async def create(self, position: Position) -> int:
        """新增一条持仓(A 方案: 同标的可多条, 纯 insert 不 upsert)。"""
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO positions (
                     market, symbol, name, quantity, cost_price, close_price,
                     profit_amount, profit_pct, opened_at, closed_at,
                     strategy_tag, entry_reason, status, note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    position.market, position.symbol, position.name,
                    position.quantity, position.cost_price, position.close_price,
                    position.profit_amount, position.profit_pct,
                    _iso(position.opened_at), _iso(position.closed_at),
                    position.strategy_tag, position.entry_reason,
                    position.status, position.note,
                    position.created_at.astimezone(timezone.utc).isoformat()
                    if position.created_at else now,
                    now,
                ),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def update(self, position: Position) -> None:
        """按 id 更新一条持仓(全字段, 含平仓价/盈亏)。"""
        if position.id is None:
            raise ValueError("update requires position.id")
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """UPDATE positions SET
                     name=?, quantity=?, cost_price=?, close_price=?,
                     profit_amount=?, profit_pct=?, opened_at=?, closed_at=?,
                     strategy_tag=?, entry_reason=?, status=?, note=?, updated_at=?
                   WHERE id=?""",
                (
                    position.name, position.quantity, position.cost_price,
                    position.close_price, position.profit_amount, position.profit_pct,
                    _iso(position.opened_at), _iso(position.closed_at),
                    position.strategy_tag, position.entry_reason,
                    position.status, position.note, now, position.id,
                ),
            )
            await db.commit()

    async def get_by_id(self, position_id: int) -> Position | None:
        async with self._connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM positions WHERE id = ?", (position_id,),
            )
            row = await cur.fetchone()
        return self._row_to_position(row) if row else None

    async def list_by_market(
        self, market: str, *, include_closed: bool = False,
    ) -> list[Position]:
        sql = [f"SELECT {self._COLS} FROM positions WHERE market = ?"]
        args: list[object] = [market]
        if not include_closed:
            sql.append("AND status != 'closed'")
        sql.append("ORDER BY updated_at DESC, id DESC")
        async with self._connect() as db:
            cur = await db.execute(" ".join(sql), args)
            rows = await cur.fetchall()
        return [self._row_to_position(r) for r in rows]

    async def close(
        self, position_id: int, *, close_price: float | None = None,
        profit_amount: float | None = None, profit_pct: float | None = None,
    ) -> None:
        """按 id 平仓: status=closed + 记平仓价/盈亏/平仓时间。"""
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """UPDATE positions SET
                     status='closed', close_price=?, profit_amount=?, profit_pct=?,
                     closed_at=?, updated_at=?
                   WHERE id=?""",
                (close_price, profit_amount, profit_pct, now, now, position_id),
            )
            await db.commit()

    async def delete(self, position_id: int) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM positions WHERE id = ?", (position_id,))
            await db.commit()
