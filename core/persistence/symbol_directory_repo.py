from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite


class SymbolDirectoryRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def upsert_many(self, items: list[tuple[str, str, str]]) -> int:
        """items: list[(symbol, name, market)]."""
        if not items:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [(s, n, m, now) for s, n, m in items]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO symbol_directory (symbol, name, market, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  name=excluded.name, market=excluded.market, updated_at=excluded.updated_at
            """, rows)
            await db.commit()
        return len(rows)

    async def get_name(self, symbol: str) -> str | None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT name FROM symbol_directory WHERE symbol = ?", (symbol,),
            )
            row = await cur.fetchone()
        return row["name"] if row else None

    async def get_names(self, symbols: list[str]) -> dict[str, str]:
        """批量查 name, 缺失的不在返回 dict 里。"""
        if not symbols:
            return {}
        placeholders = ",".join("?" * len(symbols))
        async with self._connect() as db:
            cur = await db.execute(
                f"SELECT symbol, name FROM symbol_directory WHERE symbol IN ({placeholders})",
                symbols,
            )
            rows = await cur.fetchall()
        return {r["symbol"]: r["name"] for r in rows}

    async def search(self, query: str, limit: int = 20) -> list[tuple[str, str, str]]:
        """模糊搜索:matched on symbol prefix OR name substring."""
        q = query.strip()
        if not q:
            return []
        like = f"%{q}%"
        prefix = f"{q.upper()}%"
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT symbol, name, market FROM symbol_directory
                WHERE symbol LIKE ? OR name LIKE ?
                ORDER BY
                  CASE WHEN symbol LIKE ? THEN 0 ELSE 1 END,
                  symbol
                LIMIT ?
            """, (prefix, like, prefix, limit))
            rows = await cur.fetchall()
        return [(r["symbol"], r["name"], r["market"]) for r in rows]

    async def count(self) -> int:
        async with self._connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM symbol_directory")
            row = await cur.fetchone()
        return int(row["c"])
