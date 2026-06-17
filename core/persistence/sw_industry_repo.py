from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import SwIndustryBar, SwIndustryInfo


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class SwIndustryRepo:
    """申万一级行业指数日线 + 元信息 (SQLite, api 可直读, 不碰 DuckDB)。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def save_bars(self, bars: list[SwIndustryBar]) -> int:
        if not bars:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            (
                b.market,
                b.industry_code,
                b.industry_name,
                b.trade_date,
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                b.amount,
                _iso(b.pulled_at or now),
            )
            for b in bars
        ]
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO sw_industry_index (
                     market, industry_code, industry_name, trade_date,
                     open, high, low, close, volume, amount, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, industry_code, trade_date) DO UPDATE SET
                     industry_name=excluded.industry_name,
                     open=excluded.open,
                     high=excluded.high,
                     low=excluded.low,
                     close=excluded.close,
                     volume=excluded.volume,
                     amount=excluded.amount,
                     pulled_at=excluded.pulled_at""",
                rows,
            )
            await db.commit()
            return db.total_changes - before

    async def save_info(self, infos: list[SwIndustryInfo]) -> int:
        if not infos:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            (
                i.market,
                i.industry_code,
                i.industry_name,
                i.member_count,
                i.pe_static,
                i.pe_ttm,
                i.pb,
                i.dividend_yield,
                _iso(i.updated_at or now),
            )
            for i in infos
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO sw_industry_info (
                     market, industry_code, industry_name, member_count,
                     pe_static, pe_ttm, pb, dividend_yield, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, industry_code) DO UPDATE SET
                     industry_name=excluded.industry_name,
                     member_count=excluded.member_count,
                     pe_static=excluded.pe_static,
                     pe_ttm=excluded.pe_ttm,
                     pb=excluded.pb,
                     dividend_yield=excluded.dividend_yield,
                     updated_at=excluded.updated_at""",
                rows,
            )
            await db.commit()
        return len(infos)

    async def list_history(
        self,
        industry_code: str,
        *,
        market: str = "ashare",
        start: str | None = None,
        end: str | None = None,
    ) -> list[SwIndustryBar]:
        """返回某行业 [start, end] 区间日线, 按 trade_date 升序。"""
        where = ["market = ?", "industry_code = ?"]
        args: list[object] = [market, industry_code]
        if start is not None:
            where.append("trade_date >= ?")
            args.append(start)
        if end is not None:
            where.append("trade_date <= ?")
            args.append(end)
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT * FROM sw_industry_index
                    WHERE {' AND '.join(where)}
                    ORDER BY trade_date ASC""",
                args,
            )
            rows = await cur.fetchall()
        return [_row_to_bar(r) for r in rows]

    async def last_dates(self, *, market: str = "ashare") -> dict[str, str]:
        """返回 {industry_code: max(trade_date)}, 用于增量缺口检测。"""
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT industry_code, MAX(trade_date) AS d
                   FROM sw_industry_index WHERE market = ?
                   GROUP BY industry_code""",
                (market,),
            )
            rows = await cur.fetchall()
        return {r["industry_code"]: r["d"] for r in rows if r["d"]}

    async def list_info(self, *, market: str = "ashare") -> list[SwIndustryInfo]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT * FROM sw_industry_info WHERE market = ? ORDER BY industry_code",
                (market,),
            )
            rows = await cur.fetchall()
        return [_row_to_info(r) for r in rows]


def _row_to_bar(row: aiosqlite.Row) -> SwIndustryBar:
    return SwIndustryBar(
        market=row["market"],
        industry_code=row["industry_code"],
        industry_name=row["industry_name"],
        trade_date=row["trade_date"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        amount=row["amount"],
        pulled_at=_dt(row["pulled_at"]),
    )


def _row_to_info(row: aiosqlite.Row) -> SwIndustryInfo:
    return SwIndustryInfo(
        market=row["market"],
        industry_code=row["industry_code"],
        industry_name=row["industry_name"],
        member_count=row["member_count"],
        pe_static=row["pe_static"],
        pe_ttm=row["pe_ttm"],
        pb=row["pb"],
        dividend_yield=row["dividend_yield"],
        updated_at=_dt(row["updated_at"]),
    )
