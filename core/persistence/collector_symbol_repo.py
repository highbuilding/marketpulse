from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import CollectorSymbol


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class CollectorSymbolRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row(self, row: aiosqlite.Row) -> CollectorSymbol:
        return CollectorSymbol(
            market=row["market"],
            symbol=row["symbol"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            source=row["source"],
            collect_snapshot=bool(row["collect_snapshot"]),
            collect_5m=bool(row["collect_5m"]),
            collect_signals=bool(row["collect_signals"]),
            note=row["note"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    async def seed_symbols(self, rows: list[CollectorSymbol]) -> int:
        if not rows:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        payload = [
            (
                r.market,
                r.symbol.strip().upper(),
                r.name,
                int(r.enabled),
                r.source,
                int(r.collect_snapshot),
                int(r.collect_5m),
                int(r.collect_signals),
                r.note,
                now,
                now,
            )
            for r in rows
        ]
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO collector_symbols (
                     market, symbol, name, enabled, source,
                     collect_snapshot, collect_5m, collect_signals,
                     note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, symbol) DO NOTHING""",
                payload,
            )
            await db.commit()
            return db.total_changes - before

    async def upsert(self, row: CollectorSymbol) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO collector_symbols (
                     market, symbol, name, enabled, source,
                     collect_snapshot, collect_5m, collect_signals,
                     note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, symbol) DO UPDATE SET
                     name=excluded.name,
                     enabled=excluded.enabled,
                     collect_snapshot=excluded.collect_snapshot,
                     collect_5m=excluded.collect_5m,
                     collect_signals=excluded.collect_signals,
                     note=excluded.note,
                     updated_at=excluded.updated_at""",
                (
                    row.market,
                    row.symbol.strip().upper(),
                    row.name,
                    int(row.enabled),
                    row.source,
                    int(row.collect_snapshot),
                    int(row.collect_5m),
                    int(row.collect_signals),
                    row.note,
                    row.created_at.astimezone(timezone.utc).isoformat()
                    if row.created_at else now,
                    now,
                ),
            )
            await db.commit()

    async def get(self, market: str, symbol: str) -> CollectorSymbol | None:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT market, symbol, name, enabled, source,
                          collect_snapshot, collect_5m, collect_signals,
                          note, created_at, updated_at
                   FROM collector_symbols
                   WHERE market = ? AND symbol = ?""",
                (market, symbol.strip().upper()),
            )
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def list(
        self,
        market: str,
        *,
        include_disabled: bool = True,
    ) -> list[CollectorSymbol]:
        where = ["market = ?"]
        args: list[object] = [market]
        if not include_disabled:
            where.append("enabled = 1")
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT market, symbol, name, enabled, source,
                           collect_snapshot, collect_5m, collect_signals,
                           note, created_at, updated_at
                    FROM collector_symbols
                    WHERE {' AND '.join(where)}
                    ORDER BY enabled DESC,
                             CASE source WHEN 'core' THEN 0 WHEN 'seed' THEN 1 ELSE 2 END,
                             symbol ASC""",
                args,
            )
            rows = await cur.fetchall()
        return [self._row(r) for r in rows]

    async def active_symbols(
        self,
        market: str,
        *,
        capability: str = "snapshot",
    ) -> list[str]:
        col = {
            "snapshot": "collect_snapshot",
            "5m": "collect_5m",
            "signals": "collect_signals",
        }.get(capability)
        if col is None:
            raise ValueError(f"unknown collector capability: {capability}")
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT symbol FROM collector_symbols
                    WHERE market = ? AND enabled = 1 AND {col} = 1
                    ORDER BY symbol ASC""",
                (market,),
            )
            rows = await cur.fetchall()
        return [str(r["symbol"]) for r in rows]

    async def remove(self, market: str, symbol: str) -> None:
        existing = await self.get(market, symbol)
        if existing is None:
            return
        async with self._connect() as db:
            if existing.source in {"core", "seed"}:
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    """UPDATE collector_symbols
                       SET enabled = 0, updated_at = ?
                       WHERE market = ? AND symbol = ?""",
                    (now, market, symbol.strip().upper()),
                )
            else:
                await db.execute(
                    "DELETE FROM collector_symbols WHERE market = ? AND symbol = ?",
                    (market, symbol.strip().upper()),
                )
            await db.commit()
