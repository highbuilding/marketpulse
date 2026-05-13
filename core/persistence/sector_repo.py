from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import Sector


class SectorRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def upsert_sector(
        self, name: str, classification: str, symbols: list[str],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO sectors (name, classification, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET classification=excluded.classification, "
                "updated_at=excluded.updated_at",
                (name, classification, now),
            )
            await db.execute(
                "DELETE FROM sector_constituents WHERE sector_name = ?", (name,),
            )
            if symbols:
                await db.executemany(
                    "INSERT INTO sector_constituents (sector_name, symbol) VALUES (?, ?)",
                    [(name, s) for s in symbols],
                )
            await db.commit()

    async def list_sectors(self) -> list[Sector]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT name, classification, updated_at FROM sectors ORDER BY name"
            )
            rows = await cur.fetchall()
        return [
            Sector(
                name=r["name"], classification=r["classification"],
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            for r in rows
        ]

    async def list_constituents(self, sector_name: str) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT symbol FROM sector_constituents WHERE sector_name = ? ORDER BY symbol",
                (sector_name,),
            )
            rows = await cur.fetchall()
        return [r["symbol"] for r in rows]

    async def sectors_of_symbol(self, symbol: str) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT sector_name FROM sector_constituents WHERE symbol = ? "
                "ORDER BY sector_name",
                (symbol,),
            )
            rows = await cur.fetchall()
        return [r["sector_name"] for r in rows]
