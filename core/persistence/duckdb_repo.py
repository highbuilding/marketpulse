from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock

import duckdb
import pandas as pd

from core.domain.models import Bar


class BarRepo:
    def __init__(self, db_path: str, *, read_only: bool = False) -> None:
        # read_only=True: api 进程使用,避免与 collector 争抢 DuckDB 文件写锁。
        # 雷区: 多进程同时持写锁会触发 IO Error: Conflicting lock is held in PID...
        self.db_path = db_path
        self.read_only = read_only
        self._lock = RLock()

    def _conn(self):
        return duckdb.connect(self.db_path, read_only=self.read_only)

    def init(self) -> None:
        if self.read_only:
            return
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

    # 列顺序与 bars 表 schema 严格一致, 供 DataFrame 批量 upsert 使用
    _COLS = (
        "market", "symbol", "ts", "interval", "open", "high", "low", "close",
        "volume", "amount", "turnover", "outstanding_share",
    )

    def insert_bars(self, bars: list[Bar]) -> None:
        with self._lock:
            if not bars:
                return
            rows = [(
                b.market, b.symbol, b.ts.astimezone(timezone.utc).replace(tzinfo=None),
                b.interval, b.open, b.high, b.low, b.close, b.volume,
                b.amount, b.turnover, b.outstanding_share,
            ) for b in bars]
            # 向量化批量 upsert: register DataFrame + INSERT ... SELECT ... ON CONFLICT.
            # 坑: 早期用 executemany 逐行 upsert, 回填全周期 5m (~77 万根/标的) 时
            # 单标的插入耗时 10+ 分钟 (20k 行 ≈ 16s, 线性放大). DataFrame 批量走
            # DuckDB 列式引擎, 77 万行 ~3s. 见 backfill 全历史窗口扩大后的性能回归.
            df = pd.DataFrame(rows, columns=list(self._COLS))
            with self._conn() as c:
                c.register("_incoming_bars", df)
                try:
                    c.execute("""
                        INSERT INTO bars (
                            market, symbol, ts, interval, open, high, low, close,
                            volume, amount, turnover, outstanding_share
                        )
                        SELECT
                            market, symbol, ts, interval, open, high, low, close,
                            volume, amount, turnover, outstanding_share
                        FROM _incoming_bars
                        ON CONFLICT (market, symbol, interval, ts) DO UPDATE SET
                            open=excluded.open, high=excluded.high, low=excluded.low,
                            close=excluded.close, volume=excluded.volume,
                            amount=excluded.amount, turnover=excluded.turnover,
                            outstanding_share=excluded.outstanding_share
                    """)
                finally:
                    c.unregister("_incoming_bars")

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
        return self._rows_to_bars(market, symbol, rows)

    def fetch_history_paged(
        self, market: str, symbol: str, interval: str,
        *, before: datetime | None, limit: int,
    ) -> list[Bar]:
        """游标分页: 返回严格早于 before 的最近 limit 根, 升序。

        币安/TradingView 反向翻页口径 —— before=None 取最新一页,
        前端拿到首页最老一根的 ts 作为下一页的 before, 一直翻到上市首日。
        返回升序 (前端 lightweight-charts setData 要求升序)。
        """
        before_naive = (
            before.astimezone(timezone.utc).replace(tzinfo=None)
            if before is not None else None
        )
        with self._lock, self._conn() as c:
            cur = c.execute("""
                SELECT ts, interval, open, high, low, close, volume,
                       amount, turnover, outstanding_share
                FROM bars
                WHERE market=? AND symbol=? AND interval=?
                  AND (CAST(? AS TIMESTAMP) IS NULL OR ts < CAST(? AS TIMESTAMP))
                ORDER BY ts DESC
                LIMIT ?
            """, (market, symbol, interval, before_naive, before_naive, limit))
            rows = cur.fetchall()
        rows.reverse()  # DESC 取最近 limit 根后翻成升序
        return self._rows_to_bars(market, symbol, rows)

    def fetch_last_ts_map(
        self, market: str, interval: str, symbols: list[str],
    ) -> dict[str, datetime]:
        """返回 {symbol: max(ts)}，仅返回有数据的 symbol。

        用于 sweep_derived 批量子查询，复用 repo 连接避免 read_only 冲突。
        """
        if not symbols:
            return {}
        placeholders = ",".join(["?"] * len(symbols))
        with self._lock, self._conn() as c:
            rows = c.execute(f"""
                SELECT symbol, MAX(ts) FROM bars
                WHERE market=? AND interval=?
                  AND symbol IN ({placeholders})
                GROUP BY symbol
            """, [market, interval] + list(symbols)).fetchall()
        return {r[0]: r[1].replace(tzinfo=timezone.utc) for r in rows}

    def fetch_first_ts_map(
        self, market: str, interval: str, symbols: list[str],
    ) -> dict[str, datetime]:
        """返回 {symbol: min(ts)}，用于检测派生周期是否漏了早期历史。"""
        if not symbols:
            return {}
        placeholders = ",".join(["?"] * len(symbols))
        with self._lock, self._conn() as c:
            rows = c.execute(f"""
                SELECT symbol, MIN(ts) FROM bars
                WHERE market=? AND interval=?
                  AND symbol IN ({placeholders})
                GROUP BY symbol
            """, [market, interval] + list(symbols)).fetchall()
        return {r[0]: r[1].replace(tzinfo=timezone.utc) for r in rows}

    @staticmethod
    def _rows_to_bars(market: str, symbol: str, rows: list) -> list[Bar]:
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
