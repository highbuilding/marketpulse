"""指标信号持久化(SQLite, 与 watchlists 同库)。

UNIQUE(symbol, interval, indicator, signal_type, bar_ts) 让重复扫描幂等 —
同一根 bar 上的同向信号只入一条, INSERT OR IGNORE 安静跳过。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import IndicatorSignal


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SignalRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def upsert_many(self, signals: list[IndicatorSignal]) -> int:
        """返回实际新增的行数(UNIQUE 命中的不算)。"""
        if not signals:
            return 0
        rows = [(
            s.symbol, s.interval, s.indicator, s.signal_type,
            s.bar_ts.astimezone(timezone.utc).isoformat(),
            s.detected_at.astimezone(timezone.utc).isoformat(),
            s.price, s.d_value,
        ) for s in signals]
        async with self._connect() as db:
            cur = await db.executemany(
                """INSERT OR IGNORE INTO indicator_signals
                   (symbol, interval, indicator, signal_type, bar_ts, detected_at, price, d_value)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            await db.commit()
            return cur.rowcount

    async def list_recent(
        self, *,
        since: datetime | None = None,
        intervals: list[str] | None = None,
        symbols: list[str] | None = None,
        only_unacknowledged: bool = False,
        limit: int = 200,
    ) -> list[IndicatorSignal]:
        sql = ["SELECT * FROM indicator_signals WHERE 1=1"]
        args: list = []
        if since is not None:
            sql.append("AND detected_at >= ?")
            args.append(since.astimezone(timezone.utc).isoformat())
        if intervals:
            sql.append(f"AND interval IN ({','.join('?' * len(intervals))})")
            args.extend(intervals)
        if symbols:
            sql.append(f"AND symbol IN ({','.join('?' * len(symbols))})")
            args.extend(symbols)
        if only_unacknowledged:
            sql.append("AND acknowledged = 0")
        sql.append("ORDER BY detected_at DESC LIMIT ?")
        args.append(limit)
        async with self._connect() as db:
            cur = await db.execute(" ".join(sql), args)
            rows = await cur.fetchall()
        return [_row_to_signal(r) for r in rows]

    async def list_by_symbol(
        self, symbol: str, *, intervals: list[str] | None = None, limit: int = 500,
    ) -> list[IndicatorSignal]:
        sql = ["SELECT * FROM indicator_signals WHERE symbol = ?"]
        args: list = [symbol]
        if intervals:
            sql.append(f"AND interval IN ({','.join('?' * len(intervals))})")
            args.extend(intervals)
        sql.append("ORDER BY bar_ts DESC LIMIT ?")
        args.append(limit)
        async with self._connect() as db:
            cur = await db.execute(" ".join(sql), args)
            rows = await cur.fetchall()
        return [_row_to_signal(r) for r in rows]

    async def latest_per_symbol(
        self, symbols: list[str], intervals: list[str],
    ) -> dict[tuple[str, str], IndicatorSignal]:
        """关注页用: 每 (symbol, interval) 最近一条信号。"""
        if not symbols or not intervals:
            return {}
        placeholders_s = ",".join("?" * len(symbols))
        placeholders_i = ",".join("?" * len(intervals))
        sql = f"""
            SELECT s.* FROM indicator_signals s
            JOIN (
              SELECT symbol, interval, MAX(bar_ts) AS max_ts
              FROM indicator_signals
              WHERE symbol IN ({placeholders_s}) AND interval IN ({placeholders_i})
              GROUP BY symbol, interval
            ) latest
              ON s.symbol = latest.symbol
             AND s.interval = latest.interval
             AND s.bar_ts = latest.max_ts
        """
        async with self._connect() as db:
            cur = await db.execute(sql, [*symbols, *intervals])
            rows = await cur.fetchall()
        out: dict[tuple[str, str], IndicatorSignal] = {}
        for r in rows:
            sig = _row_to_signal(r)
            out[(sig.symbol, sig.interval)] = sig
        return out

    async def acknowledge(self, signal_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE indicator_signals SET acknowledged = 1 WHERE id = ?",
                (signal_id,),
            )
            await db.commit()

    async def count_unacknowledged(self) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM indicator_signals WHERE acknowledged = 0"
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0


def _row_to_signal(r) -> IndicatorSignal:
    return IndicatorSignal(
        id=r["id"],
        symbol=r["symbol"],
        interval=r["interval"],
        indicator=r["indicator"],
        signal_type=r["signal_type"],
        bar_ts=_parse_ts(r["bar_ts"]),
        detected_at=_parse_ts(r["detected_at"]),
        price=float(r["price"]),
        d_value=float(r["d_value"]) if r["d_value"] is not None else None,
        acknowledged=bool(r["acknowledged"]),
    )
