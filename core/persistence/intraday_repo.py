"""A 股/美股当日分时点(时分线)存储。

独立 DuckDB 文件(data/intraday_{market}.duckdb), 与 K 线 bars 库物理隔离,
避免高频分时写和 K 线写抢同一 RW 连接(DuckDB 单写多读互斥)。
存逐分钟: price + 累计成交额 + 累计成交量 + 均价(写入时算好)。保留 90 天。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb


@dataclass
class IntradayPoint:
    symbol: str
    ts: datetime
    price: float
    cum_amount: float
    cum_volume: int
    avg_price: float


class IntradayLineRepo:
    def __init__(self, db_path: str, *, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only
        self._lock = threading.Lock()
        if not read_only:
            self._ensure_schema()

    @contextmanager
    def _conn(self):
        con = duckdb.connect(self.db_path, read_only=self.read_only)
        try:
            yield con
        finally:
            con.close()

    def _ensure_schema(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS intraday_lines (
                    symbol VARCHAR, ts TIMESTAMP, price DOUBLE,
                    cum_amount DOUBLE, cum_volume BIGINT, avg_price DOUBLE,
                    PRIMARY KEY (symbol, ts)
                )
                """
            )

    @staticmethod
    def _to_naive_utc(ts: datetime) -> datetime:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)

    def insert_points(self, points: list[IntradayPoint]) -> None:
        if not points:
            return
        rows = [
            (p.symbol, self._to_naive_utc(p.ts), p.price,
             p.cum_amount, p.cum_volume, p.avg_price)
            for p in points
        ]
        with self._lock, self._conn() as c:
            c.executemany(
                """
                INSERT INTO intraday_lines
                    (symbol, ts, price, cum_amount, cum_volume, avg_price)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, ts) DO UPDATE SET
                    price=excluded.price, cum_amount=excluded.cum_amount,
                    cum_volume=excluded.cum_volume, avg_price=excluded.avg_price
                """,
                rows,
            )

    def fetch_day(self, symbol: str, day: date) -> list[dict]:
        with self._lock, self._conn() as c:
            rs = c.execute(
                """
                SELECT ts, price, cum_amount, cum_volume, avg_price
                FROM intraday_lines
                WHERE symbol = ? AND CAST(ts AS DATE) = ?
                ORDER BY ts ASC
                """,
                (symbol, day),
            ).fetchall()
        return [
            {"ts": r[0].replace(tzinfo=timezone.utc).isoformat(),
             "price": r[1], "cum_amount": r[2],
             "cum_volume": r[3], "avg_price": r[4]}
            for r in rs
        ]

    def purge_before(self, cutoff: datetime) -> None:
        cut = self._to_naive_utc(cutoff)
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM intraday_lines WHERE ts < ?", (cut,))
