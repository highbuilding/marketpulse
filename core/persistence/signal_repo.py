"""指标信号持久化(SQLite, 与 watchlists 同库)。

UNIQUE(symbol, interval, indicator, signal_type, bar_ts) 让重复扫描幂等 —
同一根 bar 上的同向信号只入一条, INSERT OR IGNORE 安静跳过。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import IndicatorSignal


@dataclass(frozen=True)
class TodaySignalCell:
    """通知模板用: 同一 (symbol, interval, signal_type) 当日的汇总信息。"""
    count: int
    latest_price: float        # 当日最新一根触发 bar 的 close
    latest_bar_ts: datetime
    trigger_times: tuple[datetime, ...] = ()  # 当日所有触发 bar 时刻, 升序


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

    async def count_by_symbol_interval(
        self, symbols: list[str], since: datetime,
    ) -> dict[tuple[str, str, str], int]:
        """通知汇总用: 统计 bar_ts >= since 窗口内每 (symbol, interval, signal_type) 的信号数。

        symbols: 指定 symbol 集合 (NotificationService 在调用前已过滤好本市场 symbol).
        since: bar_ts 下限, 通常是"本市场自然日 00:00 → UTC"
            — 用 bar_ts 而非 detected_at, 这样新加 watchlist 的 symbol 第一次扫描入库的
              历史信号(detected_at=今天 但 bar_ts=数月前)不会被算入"本日新信号"。
        return: {(symbol, interval, signal_type): count}
        """
        if not symbols:
            return {}
        placeholders = ",".join("?" * len(symbols))
        sql = f"""
            SELECT symbol, interval, signal_type, COUNT(*) AS n
            FROM indicator_signals
            WHERE symbol IN ({placeholders}) AND bar_ts >= ?
            GROUP BY symbol, interval, signal_type
        """
        args = [*symbols, since.astimezone(timezone.utc).isoformat()]
        async with self._connect() as db:
            cur = await db.execute(sql, args)
            rows = await cur.fetchall()
        return {
            (r["symbol"], r["interval"], r["signal_type"]): int(r["n"])
            for r in rows
        }

    async def latest_signals_today(
        self, symbols: list[str], since: datetime,
    ) -> dict[tuple[str, str, str], TodaySignalCell]:
        """通知汇总用 (HTML 模板): 同一查询拿到 count + 当日最新 (price, bar_ts) + 所有触发 bar_ts 列表。

        SQLite 3.25+ 窗口函数 + GROUP_CONCAT 聚合所有 bar_ts。
        """
        if not symbols:
            return {}
        placeholders = ",".join("?" * len(symbols))
        sql = f"""
            SELECT
              symbol, interval, signal_type,
              COUNT(*) AS n,
              MAX(bar_ts) AS latest_bar_ts,
              -- GROUP_CONCAT 默认按出现顺序;为稳定排序, 改下面子查询返回排序后的 bar_ts 字符串
              GROUP_CONCAT(bar_ts, '|') AS bar_ts_list
            FROM (
              SELECT symbol, interval, signal_type, bar_ts, price
              FROM indicator_signals
              WHERE symbol IN ({placeholders}) AND bar_ts >= ?
              ORDER BY bar_ts ASC
            )
            GROUP BY symbol, interval, signal_type
        """
        args = [*symbols, since.astimezone(timezone.utc).isoformat()]
        async with self._connect() as db:
            cur = await db.execute(sql, args)
            rows = await cur.fetchall()

        # 单独再查一次取每组 latest 的 price (GROUP_CONCAT 拿不到对齐 price, 这里
        # 用 latest_bar_ts 反查最简单; 数据规模小, 性能够)
        out: dict[tuple[str, str, str], TodaySignalCell] = {}
        async with self._connect() as db:
            for r in rows:
                trigger_times = tuple(_parse_ts(s) for s in r["bar_ts_list"].split("|"))
                # 查 latest 那根的 price
                price_cur = await db.execute(
                    """SELECT price FROM indicator_signals
                       WHERE symbol = ? AND interval = ? AND signal_type = ? AND bar_ts = ?
                       LIMIT 1""",
                    (r["symbol"], r["interval"], r["signal_type"], r["latest_bar_ts"]),
                )
                price_row = await price_cur.fetchone()
                latest_price = float(price_row["price"]) if price_row else 0.0
                out[(r["symbol"], r["interval"], r["signal_type"])] = TodaySignalCell(
                    count=int(r["n"]),
                    latest_price=latest_price,
                    latest_bar_ts=_parse_ts(r["latest_bar_ts"]),
                    trigger_times=trigger_times,
                )
        return out


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
