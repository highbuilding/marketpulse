from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import LimitPoolItem


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


class LimitPoolRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def save_items(self, items: list[LimitPoolItem]) -> int:
        if not items:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            (
                item.market,
                item.trade_date,
                item.pool_type,
                item.symbol,
                item.name,
                item.change_pct,
                item.price,
                item.limit_price,
                item.amount,
                item.free_float_cap,
                item.total_cap,
                item.turnover_rate,
                item.seal_amount,
                item.first_seal_time,
                item.last_seal_time,
                item.break_count,
                item.ladder_count,
                item.industry,
                item.amplitude,
                _json(item.raw),
                _iso(item.pulled_at or now),
            )
            for item in items
        ]
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO limit_pool_daily (
                     market, trade_date, pool_type, symbol, name, change_pct, price,
                     limit_price, amount, free_float_cap, total_cap, turnover_rate,
                     seal_amount, first_seal_time, last_seal_time, break_count,
                     ladder_count, industry, amplitude, raw_json, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date, pool_type, symbol) DO UPDATE SET
                     name=excluded.name,
                     change_pct=excluded.change_pct,
                     price=excluded.price,
                     limit_price=excluded.limit_price,
                     amount=excluded.amount,
                     free_float_cap=excluded.free_float_cap,
                     total_cap=excluded.total_cap,
                     turnover_rate=excluded.turnover_rate,
                     seal_amount=excluded.seal_amount,
                     first_seal_time=excluded.first_seal_time,
                     last_seal_time=excluded.last_seal_time,
                     break_count=excluded.break_count,
                     ladder_count=excluded.ladder_count,
                     industry=excluded.industry,
                     amplitude=excluded.amplitude,
                     raw_json=excluded.raw_json,
                     pulled_at=excluded.pulled_at""",
                rows,
            )
            await db.commit()
            return db.total_changes - before

    async def list_by_date(
        self,
        market: str,
        trade_date: str,
        *,
        pool_type: str | None = None,
    ) -> list[LimitPoolItem]:
        where = ["market = ?", "trade_date = ?"]
        args: list[object] = [market, trade_date]
        if pool_type is not None:
            where.append("pool_type = ?")
            args.append(pool_type)
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT * FROM limit_pool_daily
                    WHERE {' AND '.join(where)}
                    ORDER BY pool_type ASC, ladder_count DESC, break_count DESC, amount DESC""",
                args,
            )
            rows = await cur.fetchall()
        return [_row_to_item(row) for row in rows]

    async def summary_by_date(self, market: str, trade_date: str) -> dict[str, Any]:
        rows = await self.list_by_date(market, trade_date)
        by_type: dict[str, list[LimitPoolItem]] = {
            "limit_up": [],
            "broken_limit": [],
            "down_limit": [],
        }
        for item in rows:
            by_type.setdefault(item.pool_type, []).append(item)

        limit_up_count = len(by_type.get("limit_up", []))
        broken_count = len(by_type.get("broken_limit", []))
        down_limit_count = len(by_type.get("down_limit", []))
        max_ladder_height = max(
            [item.ladder_count or 0 for item in by_type.get("limit_up", [])],
            default=0,
        )
        ladder_counts: dict[int, int] = {}
        for item in by_type.get("limit_up", []):
            if item.ladder_count is not None:
                ladder_counts[item.ladder_count] = ladder_counts.get(item.ladder_count, 0) + 1
        ladder_break_count = sum(
            1 for h in range(1, max_ladder_height + 1)
            if ladder_counts.get(h, 0) == 0
        )
        break_rate = broken_count / max(limit_up_count + broken_count, 1)
        return {
            "limit_up_count": limit_up_count,
            "broken_count": broken_count,
            "down_limit_count": down_limit_count,
            "break_rate": break_rate,
            "max_ladder_height": max_ladder_height,
            "ladder_counts": ladder_counts,
            "ladder_break_count": ladder_break_count,
            "sample_symbols": {
                "limit_up": [i.symbol for i in by_type.get("limit_up", [])[:10]],
                "broken_limit": [i.symbol for i in by_type.get("broken_limit", [])[:10]],
                "down_limit": [i.symbol for i in by_type.get("down_limit", [])[:10]],
            },
        }


def _row_to_item(row: aiosqlite.Row) -> LimitPoolItem:
    return LimitPoolItem(
        market=row["market"],
        trade_date=row["trade_date"],
        pool_type=row["pool_type"],
        symbol=row["symbol"],
        name=row["name"],
        change_pct=row["change_pct"],
        price=row["price"],
        limit_price=row["limit_price"],
        amount=row["amount"],
        free_float_cap=row["free_float_cap"],
        total_cap=row["total_cap"],
        turnover_rate=row["turnover_rate"],
        seal_amount=row["seal_amount"],
        first_seal_time=row["first_seal_time"],
        last_seal_time=row["last_seal_time"],
        break_count=row["break_count"],
        ladder_count=row["ladder_count"],
        industry=row["industry"],
        amplitude=row["amplitude"],
        raw=_loads(row["raw_json"]),
        pulled_at=_dt(row["pulled_at"]),
    )
