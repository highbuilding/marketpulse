from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import Watchlist


class WatchlistRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def create_watchlist(self, name: str) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO watchlists (name, is_archived, created_at) VALUES (?, 0, ?)",
                (name, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            return cur.lastrowid

    async def list_watchlists(self, include_archived: bool = False) -> list[Watchlist]:
        sql = "SELECT id, name, is_archived, created_at FROM watchlists"
        if not include_archived:
            sql += " WHERE is_archived = 0"
        sql += " ORDER BY id"
        async with self._connect() as db:
            cur = await db.execute(sql)
            rows = await cur.fetchall()
        return [
            Watchlist(
                id=r["id"], name=r["name"],
                is_archived=bool(r["is_archived"]),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def rename_watchlist(self, wl_id: int, new_name: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE watchlists SET name = ? WHERE id = ?", (new_name, wl_id),
            )
            await db.commit()

    async def archive_watchlist(self, wl_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE watchlists SET is_archived = 1 WHERE id = ?", (wl_id,),
            )
            await db.commit()

    async def add_symbol(self, wl_id: int, symbol: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, added_at) "
                "VALUES (?, ?, ?)",
                (wl_id, symbol, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def remove_symbol(self, wl_id: int, symbol: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?",
                (wl_id, symbol),
            )
            await db.commit()

    async def list_symbols(self, wl_id: int) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY symbol",
                (wl_id,),
            )
            rows = await cur.fetchall()
        return [r["symbol"] for r in rows]

    async def all_active_symbols(self) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT DISTINCT wi.symbol
                FROM watchlist_items wi
                JOIN watchlists w ON w.id = wi.watchlist_id
                WHERE w.is_archived = 0
            """)
            rows = await cur.fetchall()
        return [r["symbol"] for r in rows]
