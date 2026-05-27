from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock

import duckdb

from core.domain.models import Bar


class BarRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = RLock()

    def _conn(self):
        return duckdb.connect(self.db_path)

    def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    market   VARCHAR NOT NULL,
                    symbol   VARCHAR NOT NULL,
                    ts       TIMESTAMP NOT NULL,
                    interval VARCHAR NOT NULL,
                    open     DECIMAL(20, 8) NOT NULL,
                    high     DECIMAL(20, 8) NOT NULL,
                    low      DECIMAL(20, 8) NOT NULL,
                    close    DECIMAL(20, 8) NOT NULL,
                    volume   BIGINT NOT NULL,
                    amount   DOUBLE,
                    turnover DOUBLE,
                    outstanding_share DOUBLE,
                    PRIMARY KEY (market, symbol, interval, ts)
                )
            """)
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS amount DOUBLE")
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS turnover DOUBLE")
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS outstanding_share DOUBLE")

    def insert_bars(self, bars: list[Bar]) -> None:
        with self._lock:
            if not bars:
                return
            rows = [(
                b.market, b.symbol, b.ts.astimezone(timezone.utc).replace(tzinfo=None),
                b.interval, b.open, b.high, b.low, b.close, b.volume,
                b.amount, b.turnover, b.outstanding_share,
            ) for b in bars]
            with self._conn() as c:
                c.executemany("""
                    INSERT INTO bars (
                        market, symbol, ts, interval, open, high, low, close, volume,
                        amount, turnover, outstanding_share
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (market, symbol, interval, ts) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, volume=excluded.volume,
                        amount=excluded.amount, turnover=excluded.turnover,
                        outstanding_share=excluded.outstanding_share
                """, rows)

    def fetch_history(
        self, market: str, symbol: str,
        start: datetime, end: datetime, interval: str = "1d",
    ) -> list[Bar]:
        with self._lock, self._conn() as c:
            cur = c.execute("""
                SELECT ts, interval, open, high, low, close, volume,
                       amount, turnover, outstanding_share
                FROM bars
                WHERE market=? AND symbol=? AND interval=?
                  AND ts BETWEEN ? AND ?
                ORDER BY ts
            """, (market, symbol, interval,
                   start.astimezone(timezone.utc).replace(tzinfo=None),
                   end.astimezone(timezone.utc).replace(tzinfo=None)))
            rows = cur.fetchall()
        out: list[Bar] = []
        for ts, iv, o, h, low, cl, v, amount, turnover, outstanding_share in rows:
            out.append(Bar(
                market=market, symbol=symbol,
                ts=ts.replace(tzinfo=timezone.utc),
                open=Decimal(str(o)), high=Decimal(str(h)),
                low=Decimal(str(low)), close=Decimal(str(cl)),
                volume=int(v), interval=iv,
                amount=float(amount) if amount is not None else None,
                turnover=float(turnover) if turnover is not None else None,
                outstanding_share=(
                    float(outstanding_share) if outstanding_share is not None else None
                ),
            ))
        return out
