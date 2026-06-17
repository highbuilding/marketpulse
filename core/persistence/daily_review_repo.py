from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


class DailyReviewRepo:
    """每日复盘结论快照 (collector 收盘后写, api 只读)。

    存整份结论 (sections/summary/next_watch/data_gaps) 序列化, 让 api 进程
    零计算直接回放任意历史日, 也避免每次请求重算日线。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def save(
        self,
        *,
        market: str,
        trade_date: str,
        formula_version: str,
        summary: str,
        sections: list[dict[str, Any]],
        next_watch: list[str],
        data_gaps: list[str],
        generated_at: datetime | None = None,
    ) -> None:
        generated_at = generated_at or datetime.now(timezone.utc)
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO daily_reviews (
                     market, trade_date, formula_version, summary,
                     sections_json, next_watch_json, data_gaps_json, generated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date) DO UPDATE SET
                     formula_version=excluded.formula_version,
                     summary=excluded.summary,
                     sections_json=excluded.sections_json,
                     next_watch_json=excluded.next_watch_json,
                     data_gaps_json=excluded.data_gaps_json,
                     generated_at=excluded.generated_at""",
                (
                    market,
                    trade_date,
                    formula_version,
                    summary,
                    _dumps(sections),
                    _dumps(next_watch),
                    _dumps(data_gaps),
                    _iso(generated_at),
                ),
            )
            await db.commit()

    async def get(self, market: str, trade_date: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT * FROM daily_reviews WHERE market = ? AND trade_date = ?",
                (market, trade_date),
            )
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def list_dates(self, market: str, *, limit: int = 250) -> list[str]:
        """返回已生成复盘的交易日 (降序), 供前端日期下拉。"""
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT trade_date FROM daily_reviews
                   WHERE market = ? ORDER BY trade_date DESC LIMIT ?""",
                (market, max(1, min(limit, 1000))),
            )
            rows = await cur.fetchall()
        return [r["trade_date"] for r in rows]


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "market": row["market"],
        "trade_date": row["trade_date"],
        "formula_version": row["formula_version"],
        "summary": row["summary"],
        "sections": _loads(row["sections_json"], []),
        "next_watch": _loads(row["next_watch_json"], []),
        "data_gaps": _loads(row["data_gaps_json"], []),
        "generated_at": _dt(row["generated_at"]),
    }
