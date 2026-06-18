from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import BoardChange, StockChange


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


class StockChangesRepo:
    """个股盘口异动 + 板块异动 SQLite 读写。

    外部 AkShare 调用只在 service 层; route/结论层只读 repo。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    # === 个股异动 ===
    async def save_changes(self, items: list[StockChange]) -> int:
        if not items:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            (
                it.market, it.trade_date, it.change_time, it.symbol, it.change_type,
                it.name, it.price, it.change_pct, _json(it.raw), _iso(it.pulled_at or now),
            )
            for it in items
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO stock_changes (
                     market, trade_date, change_time, symbol, change_type,
                     name, price, change_pct, raw_json, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date, symbol, change_type, change_time)
                   DO UPDATE SET
                     name=excluded.name, price=excluded.price,
                     change_pct=excluded.change_pct, raw_json=excluded.raw_json,
                     pulled_at=excluded.pulled_at""",
                rows,
            )
            await db.commit()
        return len(rows)

    async def list_changes(
        self,
        market: str,
        trade_date: str,
        *,
        change_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[StockChange]:
        where = ["market = ?", "trade_date = ?"]
        args: list[object] = [market, trade_date]
        if change_types:
            placeholders = ",".join("?" for _ in change_types)
            where.append(f"change_type IN ({placeholders})")
            args.extend(change_types)
        args.append(limit)
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT * FROM stock_changes
                    WHERE {' AND '.join(where)}
                    ORDER BY change_time DESC
                    LIMIT ?""",
                args,
            )
            rows = await cur.fetchall()
        return [_row_to_change(r) for r in rows]

    async def count_by_type(self, market: str, trade_date: str) -> dict[str, int]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT change_type, COUNT(*) AS n FROM stock_changes
                   WHERE market = ? AND trade_date = ?
                   GROUP BY change_type""",
                (market, trade_date),
            )
            rows = await cur.fetchall()
        return {r["change_type"]: r["n"] for r in rows}

    # === 板块异动 ===
    async def save_board_changes(self, items: list[BoardChange]) -> int:
        if not items:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            (
                it.market, it.trade_date, it.board_name, it.change_pct,
                it.main_net_inflow, it.change_total, it.top_symbol, it.top_name,
                it.top_direction, _json(it.raw), _iso(it.pulled_at or now),
            )
            for it in items
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO board_changes (
                     market, trade_date, board_name, change_pct, main_net_inflow,
                     change_total, top_symbol, top_name, top_direction, raw_json, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date, board_name) DO UPDATE SET
                     change_pct=excluded.change_pct,
                     main_net_inflow=excluded.main_net_inflow,
                     change_total=excluded.change_total,
                     top_symbol=excluded.top_symbol, top_name=excluded.top_name,
                     top_direction=excluded.top_direction,
                     raw_json=excluded.raw_json, pulled_at=excluded.pulled_at""",
                rows,
            )
            await db.commit()
        return len(rows)

    async def list_board_changes(
        self, market: str, trade_date: str, *, limit: int = 50,
    ) -> list[BoardChange]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT * FROM board_changes
                   WHERE market = ? AND trade_date = ?
                   ORDER BY change_total DESC
                   LIMIT ?""",
                (market, trade_date, limit),
            )
            rows = await cur.fetchall()
        return [_row_to_board(r) for r in rows]


def _row_to_change(row: aiosqlite.Row) -> StockChange:
    return StockChange(
        market=row["market"],
        trade_date=row["trade_date"],
        change_time=row["change_time"],
        symbol=row["symbol"],
        change_type=row["change_type"],
        name=row["name"],
        price=row["price"],
        change_pct=row["change_pct"],
        raw=_loads(row["raw_json"]),
        pulled_at=_dt(row["pulled_at"]),
    )


def _row_to_board(row: aiosqlite.Row) -> BoardChange:
    return BoardChange(
        market=row["market"],
        trade_date=row["trade_date"],
        board_name=row["board_name"],
        change_pct=row["change_pct"],
        main_net_inflow=row["main_net_inflow"],
        change_total=row["change_total"],
        top_symbol=row["top_symbol"],
        top_name=row["top_name"],
        top_direction=row["top_direction"],
        raw=_loads(row["raw_json"]),
        pulled_at=_dt(row["pulled_at"]),
    )
