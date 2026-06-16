from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiosqlite

from core.domain.models import FundFlowSnapshot


class FundFlowRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def save_symbol_flows(self, items: list[FundFlowSnapshot]) -> None:
        if not items:
            return
        rows = [(
            f.subject, f.ts.astimezone(timezone.utc).isoformat(),
            f.main_net, f.super_large_net, f.large_net, f.medium_net, f.small_net,
        ) for f in items]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO fund_flow_symbol (symbol, ts, main_net, super_large_net,
                                              large_net, medium_net, small_net)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, ts) DO UPDATE SET
                  main_net=excluded.main_net,
                  super_large_net=excluded.super_large_net,
                  large_net=excluded.large_net,
                  medium_net=excluded.medium_net,
                  small_net=excluded.small_net
            """, rows)
            await db.commit()

    async def query_symbol_flow(
        self, symbol: str, start: datetime, end: datetime,
    ) -> list[FundFlowSnapshot]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT symbol, ts, main_net, super_large_net, large_net, medium_net, small_net
                FROM fund_flow_symbol
                WHERE symbol = ? AND ts BETWEEN ? AND ?
                ORDER BY ts
            """, (symbol, start.astimezone(timezone.utc).isoformat(),
                   end.astimezone(timezone.utc).isoformat()))
            rows = await cur.fetchall()
        return [_to_symbol_snapshot(r) for r in rows]

    async def latest_symbol_flows(
        self,
        symbols: list[str],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, FundFlowSnapshot]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        args: list[object] = [
            *symbols,
            start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(),
        ]
        async with self._connect() as db:
            cur = await db.execute(
                f"""
                SELECT s.symbol, s.ts, s.main_net, s.super_large_net,
                       s.large_net, s.medium_net, s.small_net
                FROM fund_flow_symbol s
                JOIN (
                    SELECT symbol, MAX(ts) AS ts
                    FROM fund_flow_symbol
                    WHERE symbol IN ({placeholders}) AND ts BETWEEN ? AND ?
                    GROUP BY symbol
                ) latest ON latest.symbol = s.symbol AND latest.ts = s.ts
                """,
                args,
            )
            rows = await cur.fetchall()
        return {r["symbol"]: _to_symbol_snapshot(r) for r in rows}

    async def save_sector_flows(self, items: list[FundFlowSnapshot]) -> None:
        if not items:
            return
        rows = [(f.subject, f.ts.astimezone(timezone.utc).isoformat(),
                  f.main_net, f.pct_change) for f in items]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO fund_flow_sector (sector_name, ts, main_net, pct_change)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sector_name, ts) DO UPDATE SET
                  main_net=excluded.main_net, pct_change=excluded.pct_change
            """, rows)
            await db.commit()

    async def query_sector_flow(
        self, sector_name: str, start: datetime, end: datetime,
    ) -> list[FundFlowSnapshot]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT sector_name, ts, main_net, pct_change
                FROM fund_flow_sector
                WHERE sector_name = ? AND ts BETWEEN ? AND ?
                ORDER BY ts
            """, (sector_name, start.astimezone(timezone.utc).isoformat(),
                   end.astimezone(timezone.utc).isoformat()))
            rows = await cur.fetchall()
        return [FundFlowSnapshot(
            subject=r["sector_name"], kind="sector",
            ts=datetime.fromisoformat(r["ts"]),
            main_net=r["main_net"], pct_change=r["pct_change"],
        ) for r in rows]

    async def save_north_flow(self, snap: FundFlowSnapshot) -> None:
        total = (snap.hgt_net or 0) + (snap.sgt_net or 0)
        async with self._connect() as db:
            await db.execute("""
                INSERT INTO fund_flow_north (ts, hgt_net, sgt_net, total_net)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ts) DO UPDATE SET
                  hgt_net=excluded.hgt_net, sgt_net=excluded.sgt_net,
                  total_net=excluded.total_net
            """, (snap.ts.astimezone(timezone.utc).isoformat(),
                   snap.hgt_net, snap.sgt_net, total))
            await db.commit()

    async def query_north_flow(
        self, start: datetime, end: datetime,
    ) -> list[FundFlowSnapshot]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT ts, hgt_net, sgt_net FROM fund_flow_north
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts
            """, (start.astimezone(timezone.utc).isoformat(),
                   end.astimezone(timezone.utc).isoformat()))
            rows = await cur.fetchall()
        return [FundFlowSnapshot(
            subject="north", kind="north",
            ts=datetime.fromisoformat(r["ts"]),
            hgt_net=r["hgt_net"], sgt_net=r["sgt_net"],
        ) for r in rows]

    async def purge_old_symbol(self, days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM fund_flow_symbol WHERE ts < ?", (cutoff,),
            )
            await db.commit()
            return cur.rowcount

    async def purge_old_sector(self, days: int = 90) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM fund_flow_sector WHERE ts < ?", (cutoff,),
            )
            await db.commit()
            return cur.rowcount

    async def purge_old_north(self, days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM fund_flow_north WHERE ts < ?", (cutoff,),
            )
            await db.commit()
            return cur.rowcount


def _to_symbol_snapshot(r) -> FundFlowSnapshot:
    return FundFlowSnapshot(
        subject=r["symbol"], kind="symbol",
        ts=datetime.fromisoformat(r["ts"]),
        main_net=r["main_net"], super_large_net=r["super_large_net"],
        large_net=r["large_net"], medium_net=r["medium_net"], small_net=r["small_net"],
    )
