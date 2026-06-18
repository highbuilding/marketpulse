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
        ladder = _ladder_ecology(by_type.get("limit_up", []))
        break_rate = broken_count / max(limit_up_count + broken_count, 1)
        return {
            "limit_up_count": limit_up_count,
            "broken_count": broken_count,
            "down_limit_count": down_limit_count,
            "break_rate": break_rate,
            "max_ladder_height": max_ladder_height,
            **ladder,
            "sample_symbols": {
                "limit_up": [i.symbol for i in by_type.get("limit_up", [])[:10]],
                "broken_limit": [i.symbol for i in by_type.get("broken_limit", [])[:10]],
                "down_limit": [i.symbol for i in by_type.get("down_limit", [])[:10]],
            },
        }

    async def previous_performance_by_date(
        self, market: str, trade_date: str,
    ) -> dict[str, Any] | None:
        """昨日涨停池在今日的表现统计。

        `previous` 来自 stock_zt_pool_previous_em, 其中 change_pct/price 是今日表现,
        ladder_count/first_seal_time 是昨日涨停结构。promotion_rate 用今日真实涨停池交集计算。
        """
        rows = await self.list_by_date(market, trade_date)
        previous = [r for r in rows if r.pool_type == "previous"]
        if not previous:
            return None
        today_limit = {r.symbol for r in rows if r.pool_type == "limit_up"}
        promoted = [r for r in previous if r.symbol in today_limit]
        eliminated = [r for r in previous if r.symbol not in today_limit]
        changes = [float(r.change_pct) for r in previous if r.change_pct is not None]
        eliminated_changes = [
            float(r.change_pct) for r in eliminated if r.change_pct is not None
        ]
        red_count = sum(1 for v in changes if v > 0)
        flat_count = sum(1 for v in changes if v == 0)
        green_count = len(changes) - red_count - flat_count
        avg_change = sum(changes) / len(changes) if changes else None
        high_previous = [i for i in previous if (i.ladder_count or 0) >= 2]
        high_promoted = [i for i in high_previous if i.symbol in today_limit]
        high_eliminated = [i for i in high_previous if i.symbol not in today_limit]
        high_eliminated_changes = [
            float(i.change_pct) for i in high_eliminated if i.change_pct is not None
        ]
        loser_penalty = (
            sum(eliminated_changes) / len(eliminated_changes)
            if eliminated_changes else None
        )
        promotion_rate = len(promoted) / max(len(previous), 1)
        red_rate = red_count / max(len(changes), 1) if changes else None
        green_rate = green_count / max(len(changes), 1) if changes else None
        high_promotion_rate = len(high_promoted) / max(len(high_previous), 1) if high_previous else None
        high_loser_penalty = (
            sum(high_eliminated_changes) / len(high_eliminated_changes)
            if high_eliminated_changes else None
        )
        return {
            "previous_count": len(previous),
            "promoted_count": len(promoted),
            "eliminated_count": len(eliminated),
            "promotion_rate": promotion_rate,
            "red_rate": red_rate,
            "green_rate": green_rate,
            "current_edge_pct": avg_change,
            "open_edge_pct": None,
            "loser_penalty_pct": loser_penalty,
            "high_previous_count": len(high_previous),
            "high_promoted_count": len(high_promoted),
            "high_promotion_rate": high_promotion_rate,
            "high_loser_penalty_pct": high_loser_penalty,
            "red_count": red_count,
            "flat_count": flat_count,
            "green_count": green_count,
            "sample_promoted": [r.symbol for r in promoted[:10]],
            "sample_eliminated": [r.symbol for r in eliminated[:10]],
            "formula": (
                "promotion_rate=count(previous∩today_limit)/count(previous); "
                "current_edge=mean(previous.change_pct); "
                "loser_penalty=mean(change_pct of previous not promoted); "
                "high_promotion_rate=count(previous ladder>=2 promoted)/count(previous ladder>=2)"
            ),
        }

    async def market_state_window(
        self, market: str, start_date: str, end_date: str,
    ) -> dict[str, dict[str, Any]]:
        """区间内涨停生态汇总,供策略回测把复盘条件转成每日结构化字段。"""
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT * FROM limit_pool_daily
                   WHERE market = ? AND trade_date >= ? AND trade_date <= ?
                   ORDER BY trade_date ASC, pool_type ASC""",
                (market, start_date, end_date),
            )
            rows = await cur.fetchall()

        grouped: dict[str, list[LimitPoolItem]] = {}
        for row in rows:
            item = _row_to_item(row)
            grouped.setdefault(item.trade_date, []).append(item)

        out: dict[str, dict[str, Any]] = {}
        for trade_date, items in grouped.items():
            by_type: dict[str, list[LimitPoolItem]] = {}
            for item in items:
                by_type.setdefault(item.pool_type, []).append(item)
            limit_up = by_type.get("limit_up", [])
            broken = by_type.get("broken_limit", [])
            down = by_type.get("down_limit", [])
            previous = by_type.get("previous", [])
            today_limit = {i.symbol for i in limit_up}
            promoted = [i for i in previous if i.symbol in today_limit]
            eliminated = [i for i in previous if i.symbol not in today_limit]
            ladder = _ladder_ecology(limit_up)
            high_previous = [i for i in previous if (i.ladder_count or 0) >= 2]
            high_promoted = [i for i in high_previous if i.symbol in today_limit]
            high_eliminated = [i for i in high_previous if i.symbol not in today_limit]
            eliminated_changes = [
                float(i.change_pct) for i in eliminated if i.change_pct is not None
            ]
            high_eliminated_changes = [
                float(i.change_pct) for i in high_eliminated if i.change_pct is not None
            ]
            changes = [float(i.change_pct) for i in previous if i.change_pct is not None]
            promotion_rate = len(promoted) / max(len(previous), 1) if previous else None
            red_count = sum(1 for v in changes if v > 0)
            green_count = sum(1 for v in changes if v < 0)
            red_rate = red_count / max(len(changes), 1) if changes else None
            high_promotion_rate = (
                len(high_promoted) / max(len(high_previous), 1)
                if high_previous else None
            )
            loser_penalty = (
                sum(eliminated_changes) / len(eliminated_changes)
                if eliminated_changes else None
            )
            high_loser_penalty = (
                sum(high_eliminated_changes) / len(high_eliminated_changes)
                if high_eliminated_changes else None
            )
            break_rate = len(broken) / max(len(limit_up) + len(broken), 1)
            ladder_strength = float(ladder["ladder_strength_score"])
            out[trade_date] = {
                "limit_up_count": len(limit_up),
                "broken_count": len(broken),
                "down_limit_count": len(down),
                "break_rate": break_rate,
                "max_ladder_height": max((i.ladder_count or 0) for i in limit_up) if limit_up else 0,
                **ladder,
                "previous_count": len(previous),
                "promotion_rate": promotion_rate,
                "red_rate": red_rate,
                "current_edge_pct": sum(changes) / len(changes) if changes else None,
                "loser_penalty_pct": loser_penalty,
                "high_previous_count": len(high_previous),
                "high_promotion_rate": high_promotion_rate,
                "high_loser_penalty_pct": high_loser_penalty,
                "short_term_allowed": (
                    (promotion_rate is not None and promotion_rate >= 0.30)
                    and (high_promotion_rate is None or high_promotion_rate >= 0.20)
                    and break_rate <= 0.35
                    and len(down) <= 10
                    and ladder_strength >= 35
                ),
                "trend_allowed": (
                    break_rate <= 0.45
                    and len(down) <= 20
                    and (loser_penalty is None or loser_penalty >= -4.0)
                    and (high_loser_penalty is None or high_loser_penalty >= -5.0)
                ),
            }
        return out

    async def pool_symbols_window(
        self, market: str, start_date: str, end_date: str, pool_type: str,
    ) -> dict[str, set[str]]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT trade_date, symbol FROM limit_pool_daily
                   WHERE market = ? AND trade_date >= ? AND trade_date <= ?
                     AND pool_type = ?""",
                (market, start_date, end_date, pool_type),
            )
            rows = await cur.fetchall()
        out: dict[str, set[str]] = {}
        for row in rows:
            out.setdefault(str(row["trade_date"]), set()).add(str(row["symbol"]))
        return out


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


def _ladder_ecology(limit_up: list[LimitPoolItem]) -> dict[str, Any]:
    ladder_counts: dict[int, int] = {}
    for item in limit_up:
        height = item.ladder_count or 1
        if height <= 0:
            height = 1
        ladder_counts[height] = ladder_counts.get(height, 0) + 1

    total = len(limit_up)
    max_height = max(ladder_counts, default=0)
    first_count = ladder_counts.get(1, 0)
    second_plus = sum(count for height, count in ladder_counts.items() if height >= 2)
    third_plus = sum(count for height, count in ladder_counts.items() if height >= 3)
    ladder_break_count = sum(1 for h in range(1, max_height + 1) if ladder_counts.get(h, 0) == 0)
    layer_count = sum(1 for h in range(1, max_height + 1) if ladder_counts.get(h, 0) > 0)
    continuity = layer_count / max(max_height, 1) if max_height else 0.0
    second_plus_rate = second_plus / max(total, 1) if total else 0.0
    first_board_rate = first_count / max(total, 1) if total else 0.0
    # 0-100: 高度、二板以上广度、梯队连续性共同刻画连板生态。
    strength = round(
        35 * min(max_height / 7, 1)
        + 35 * min(second_plus / 20, 1)
        + 20 * continuity
        + 10 * min(third_plus / 8, 1),
        2,
    )
    return {
        "ladder_counts": ladder_counts,
        "ladder_break_count": ladder_break_count,
        "ladder_layer_count": layer_count,
        "second_plus_count": second_plus,
        "third_plus_count": third_plus,
        "second_plus_rate": second_plus_rate,
        "first_board_rate": first_board_rate,
        "ladder_continuity": continuity,
        "ladder_strength_score": strength,
    }
