"""市场级"今日成交额 vs 同时段基线"持久化 (2026-05-28 设计)。

数据流:
- 每日收盘后 cron `market_amount_baseline_persist` 写当日 5min 累计曲线
- 次日盘中 *_index_minute job 查同 ts_5m_offset 算 amount_ratio
- crypto 不入表 (Binance 24h ticker 现成)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta

import aiosqlite


class MarketAmountBaselineRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def upsert_day(
        self, market: str, trading_date: str,
        points: list[tuple[int, float]],
    ) -> int:
        """一次写入当日所有 5m offset 累计曲线 (UPSERT)。

        Args:
            market: 'ashare' / 'hk' / 'us'
            trading_date: 'YYYY-MM-DD' (本市场所在地自然日)
            points: [(ts_5m_offset, cum_amount), ...]

        Returns:
            实际写入行数
        """
        if not points:
            return 0
        rows = [(market, trading_date, off, amt) for off, amt in points]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO market_amount_baseline (market, trading_date, ts_5m_offset, cum_amount)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market, trading_date, ts_5m_offset) DO UPDATE SET
                  cum_amount = excluded.cum_amount
            """, rows)
            await db.commit()
        return len(rows)

    async def query_prev_day_at_offset(
        self, market: str, today: str, ts_5m_offset: int,
    ) -> float | None:
        """返回上一交易日同 offset 的累计成交额。

        Args:
            market: 'ashare' / 'hk' / 'us'
            today: 'YYYY-MM-DD' (今日, 严格小于此日期)
            ts_5m_offset: 5m 桶序号

        Returns:
            上一交易日同 offset cum_amount, 无数据则 None
        """
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT cum_amount FROM market_amount_baseline
                WHERE market = ? AND ts_5m_offset = ? AND trading_date < ?
                ORDER BY trading_date DESC LIMIT 1
            """, (market, ts_5m_offset, today))
            row = await cur.fetchone()
        return float(row["cum_amount"]) if row else None

    async def query_avg_n_days_at_offset(
        self, market: str, today: str, ts_5m_offset: int, n_days: int = 10,
    ) -> float | None:
        """返回过去 N 个交易日同 offset 的平均累计成交额 (Relative Volume 用)。

        Args:
            n_days: 默认 10 (Bloomberg Relative Volume 10D 口径)
        """
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT AVG(cum_amount) AS avg_amt FROM (
                    SELECT cum_amount FROM market_amount_baseline
                    WHERE market = ? AND ts_5m_offset = ? AND trading_date < ?
                    ORDER BY trading_date DESC LIMIT ?
                )
            """, (market, ts_5m_offset, today, n_days))
            row = await cur.fetchone()
        if row is None or row["avg_amt"] is None:
            return None
        return float(row["avg_amt"])

    async def cleanup_older_than(self, days: int = 20) -> int:
        """删除 N 天前数据 (默认 20 天, 给同比留足缓冲)。

        Returns:
            删除行数
        """
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM market_amount_baseline WHERE trading_date < ?",
                (cutoff,),
            )
            await db.commit()
        return cur.rowcount or 0
